"""Pilot support: logging unanswered questions as requests, and the audit log reader.

- POST /api/v1/answers/{query_id}/request: the asker chooses to send their question to the
  curators of one of their units (plan: "offering to log it as a request").
- GET /api/v1/requests and POST /api/v1/requests/{id}/resolve: curators follow up.
- GET /api/v1/audit: the audit trail for ``audit_reader`` holders, limited to activity by
  people who hold a role in the reader's audit units (plan: "scoped to the caller's
  business unit"). Cursor-paginated, newest first.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from apps.api.auth import CurrentUser, require_auth_context
from db.audit_reader import AuditRow, audit_page
from db.schema import RequestStatus
from db.support import asker_of, lock_request, log_request, requests_for
from db.tenancy import tenant_transaction
from lekha.auth import CURATOR_ROLE, AuthContext

AUDIT_READER_ROLE = "audit_reader"
MAX_AUDIT_PAGE = 100

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)


def _curated_units(ctx: AuthContext) -> list[uuid.UUID]:
    return [bu for bu, roles in ctx.roles_by_bu.items() if CURATOR_ROLE in roles]


class RequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_unit_id: uuid.UUID
    note: str = Field(default="", max_length=500)


class RequestOut(BaseModel):
    id: uuid.UUID
    question: str
    asked_by: str
    business_unit_id: uuid.UUID
    note: str
    status: RequestStatus
    resolution: str
    created_at: datetime
    resolved_at: datetime | None
    mine: bool


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["resolved", "dismissed"]
    resolution: str = Field(default="", max_length=500)


@router.post(
    "/answers/{query_id}/request",
    status_code=201,
    responses={
        403: {"description": "You have no role in that business unit"},
        404: {"description": "No such question, or it was asked by someone else"},
        409: {"description": "Already logged"},
    },
)
async def log_question(
    request: Request, ctx: CurrentUser, query_id: uuid.UUID, body: RequestIn
) -> JSONResponse:
    if not ctx.roles_by_bu.get(body.business_unit_id):
        raise HTTPException(status_code=403, detail="You can only log requests in your own units.")
    try:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            if await asker_of(s, query_id) != ctx.user_id:
                raise HTTPException(status_code=404, detail="Not found.")
            request_id = await log_request(
                s,
                organisation_id=ctx.organisation_id,
                query_id=query_id,
                user_id=ctx.user_id,
                business_unit_id=body.business_unit_id,
                note=body.note,
            )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="This question is already logged.") from None
    request.scope.setdefault("state", {})["audit_target"] = ("question_request", str(request_id))
    return JSONResponse(status_code=201, content={"id": str(request_id), "status": "open"})


@router.get("/requests", response_model=list[RequestOut])
async def list_requests(
    request: Request, ctx: CurrentUser, status: RequestStatus | None = None
) -> list[RequestOut]:
    """Requests in units you curate, plus the ones you logged yourself."""
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        views = await requests_for(
            s, user_id=ctx.user_id, curator_units=_curated_units(ctx), status=status
        )
    return [RequestOut.model_validate(v, from_attributes=True) for v in views]


@router.post(
    "/requests/{question_request_id}/resolve",
    response_model=RequestOut,
    responses={
        404: {"description": "Not found, or not a curator of its unit"},
        409: {"description": "Already closed"},
    },
)
async def resolve_request(
    request: Request, ctx: CurrentUser, question_request_id: uuid.UUID, body: ResolveIn
) -> RequestOut:
    now = datetime.now(UTC)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        found = await lock_request(s, question_request_id)
        # Not a curator of its unit: indistinguishable from a request that doesn't exist.
        if found is None or found.business_unit_id not in _curated_units(ctx):
            raise HTTPException(status_code=404, detail="Not found.")
        if found.status is not RequestStatus.OPEN:
            raise HTTPException(status_code=409, detail="This request is already closed.")
        found.status = RequestStatus(body.status)
        found.resolution = body.resolution
        found.resolved_by = ctx.user_id
        found.resolved_at = now
        await s.flush()
        views = await requests_for(
            s, user_id=ctx.user_id, curator_units=_curated_units(ctx), status=None
        )
    return RequestOut.model_validate(
        next(v for v in views if v.id == question_request_id), from_attributes=True
    )


# --- audit reader --------------------------------------------------------------------


class AuditEventOut(BaseModel):
    occurred_at: datetime
    actor: str | None
    action: str
    method: str
    path: str
    status_code: int
    outcome: str
    target_type: str | None
    target_id: uuid.UUID | None
    request_id: str


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


def _encode(row: AuditRow) -> str:
    raw = f"{row.occurred_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        when, ident = raw.split("|", 1)
        return datetime.fromisoformat(when), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid cursor.") from None


@router.get(
    "/audit",
    response_model=AuditPage,
    responses={403: {"description": "Requires the audit_reader role"}},
)
async def read_audit(
    request: Request,
    ctx: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_PAGE)] = 50,
    cursor: str | None = None,
) -> AuditPage:
    units = [bu for bu, roles in ctx.roles_by_bu.items() if AUDIT_READER_ROLE in roles]
    if not units:
        raise HTTPException(
            status_code=403, detail="Reading the audit log needs the audit_reader role."
        )
    after = _decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        rows = await audit_page(s, units=units, limit=limit + 1, after=after)
    page, more = rows[:limit], len(rows) > limit
    return AuditPage(
        items=[
            AuditEventOut(
                occurred_at=r.occurred_at,
                actor=r.actor,
                action=r.action,
                method=r.method,
                path=r.path,
                status_code=r.status_code,
                outcome=r.outcome,
                target_type=r.target_type,
                target_id=r.target_id,
                request_id=r.request_id,
            )
            for r in page
        ],
        next_cursor=_encode(page[-1]) if more and page else None,
    )
