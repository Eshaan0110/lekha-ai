"""POST /api/v1/deletion-requests (plan endpoint) and GET /api/v1/deletion-requests/{id}.

Deleting is two steps. This request hides the document at once (soft delete, in the same
transaction that records the request), so from the response onward nobody can read or find
it. The worker then purges its vectors, chunks and stored files and resolves the request.
Only curators of the document's business unit, cleared for its classification, may delete.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError

from apps.api.auth import CurrentUser, require_auth_context
from apps.wiring import JobQueue
from db.deletion import (
    caller_can_read,
    lock_live_document,
    soft_delete_document,
    visible_deletion_request,
)
from db.schema import DeletionStatus
from db.tenancy import tenant_transaction
from lekha.auth import CURATOR_ROLE, can_manage

logger = structlog.get_logger("lekha.deletion")

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)


class DeletionRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["document"]  # versions and user data: later phases
    document_id: uuid.UUID


class DeletionRequestOut(BaseModel):
    id: uuid.UUID
    scope: str
    document_id: uuid.UUID
    status: DeletionStatus
    error_code: str | None
    chunks_removed: int | None
    files_removed: int | None
    created_at: datetime
    completed_at: datetime | None


@router.post(
    "/deletion-requests",
    status_code=202,
    response_model=DeletionRequestOut,
    responses={
        403: {"description": "May read the document but not delete it"},
        404: {"description": "No such document, already deleted, or not visible"},
    },
)
async def request_deletion(
    body: DeletionRequestIn, request: Request, ctx: CurrentUser
) -> JSONResponse:
    now = datetime.now(UTC)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        document = await lock_live_document(s, body.document_id)
        allowed = document is not None and can_manage(
            ctx, business_unit_id=document.business_unit_id, classification=document.classification
        )
        if not allowed:
            # Readers learn they may not delete; everyone else can't tell it exists.
            if document is not None and await caller_can_read(s, ctx, body.document_id, now=now):
                raise HTTPException(status_code=403, detail="You may not delete this document.")
            raise HTTPException(status_code=404, detail="Document not found.")
        request_id = await soft_delete_document(s, ctx, body.document_id, now=now)
    request.scope.setdefault("state", {})["audit_target"] = ("document", str(body.document_id))

    queue: JobQueue = request.app.state.job_queue
    try:
        await queue.enqueue_purge(ctx.organisation_id, request_id)
    except (OSError, RedisError):
        # Already hidden from everyone; the purge stays pending and can be re-queued.
        logger.exception("purge_enqueue_failed")
    out = await _load(request, ctx, request_id)
    location = f"/api/v1/deletion-requests/{request_id}"
    return JSONResponse(
        status_code=202, content=out.model_dump(mode="json"), headers={"Location": location}
    )


@router.get(
    "/deletion-requests/{request_id}",
    response_model=DeletionRequestOut,
    responses={404: {"description": "No such request, or not visible to you"}},
)
async def get_deletion_request(
    request_id: uuid.UUID, request: Request, ctx: CurrentUser
) -> DeletionRequestOut:
    return await _load(request, ctx, request_id)


async def _load(request: Request, ctx: CurrentUser, request_id: uuid.UUID) -> DeletionRequestOut:
    curator_units = [bu for bu, roles in ctx.roles_by_bu.items() if CURATOR_ROLE in roles]
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        found = await visible_deletion_request(s, ctx, request_id, curator_units)
        if found is None:
            raise HTTPException(status_code=404, detail="Deletion request not found.")
        return DeletionRequestOut.model_validate(found, from_attributes=True)
