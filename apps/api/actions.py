"""Actions: Lekha proposes, a named human approves, then the worker executes once
(plan "Safe Actions"; CLAUDE.md §6.3). Phase 5 has one tool: ``create_ticket``, against
the SIMULATED ticketing system.

- POST /api/v1/actions/propose: anyone with a role in the unit proposes (for themselves).
  An optional ``Idempotency-Key`` header makes a retried proposal return the first one.
- GET /api/v1/actions and GET /api/v1/actions/{id}: proposals in units you approve for,
  plus your own.
- POST /api/v1/actions/{id}/approve (optionally with edited inputs, validated again) and
  POST /api/v1/actions/{id}/reject: only an ``approver`` of that unit, never for their own
  proposal (separation of duties). Undecided proposals expire after 24 hours.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.idempotency import idempotency_key
from apps.wiring import JobQueue
from db.actions import (
    ProposalView,
    add_proposal,
    lock_proposal,
    proposal_by_key,
    proposals_for,
    record_decision,
)
from db.tenancy import tenant_transaction
from lekha.actions import (
    APPROVER_ROLE,
    PROPOSAL_LIFETIME,
    ApprovalDenial,
    ProposalStatus,
    UnknownToolError,
    approval_denial,
    can_propose,
    validate_inputs,
)
from lekha.auth import AuthContext

logger = structlog.get_logger("lekha.actions")

router = APIRouter(
    prefix="/api/v1/actions",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)


class ProposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_unit_id: uuid.UUID
    tool: str = Field(max_length=60)
    inputs: dict[str, Any]  # Any: tool JSON, validated against the tool's own schema


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    edited_inputs: dict[str, Any] | None = None  # Any: tool JSON, validated again
    reason: str = Field(default="", max_length=500)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class ProposalOut(BaseModel):
    id: uuid.UUID
    business_unit_id: uuid.UUID
    tool: str
    inputs: dict[str, Any]  # Any: tool JSON
    proposed_by: str
    mine: bool
    status: ProposalStatus
    created_at: datetime
    expires_at: datetime
    decided_by: str | None
    reason: str | None
    external_id: str | None = Field(description="The ticket reference once executed (SIM-…)")
    error_code: str | None
    can_decide: bool


def _approver_units(ctx: AuthContext) -> list[uuid.UUID]:
    return [bu for bu, roles in ctx.roles_by_bu.items() if APPROVER_ROLE in roles]


def _out(view: ProposalView, ctx: AuthContext, now: datetime) -> ProposalOut:
    status = view.status
    if status is ProposalStatus.PENDING and view.expires_at <= now:
        status = ProposalStatus.EXPIRED  # lapsed; recorded when someone tries to decide
    can_decide = (
        status is ProposalStatus.PENDING
        and not view.mine
        and APPROVER_ROLE in ctx.roles_by_bu.get(view.business_unit_id, frozenset())
    )
    return ProposalOut(
        id=view.id,
        business_unit_id=view.business_unit_id,
        tool=view.tool,
        inputs=view.inputs,
        proposed_by=view.proposed_by,
        mine=view.mine,
        status=status,
        created_at=view.created_at,
        expires_at=view.expires_at,
        decided_by=view.decided_by,
        reason=view.reason,
        external_id=view.external_id,
        error_code=view.error_code,
        can_decide=can_decide,
    )


def _validated(tool: str, inputs: dict[str, Any]) -> dict[str, Any]:  # Any: tool JSON
    try:
        return validate_inputs(tool, inputs)
    except UnknownToolError:
        raise HTTPException(status_code=422, detail="Unknown tool.") from None
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "inputs" for e in exc.errors()})
        raise HTTPException(
            status_code=422, detail=f"Invalid inputs: {', '.join(fields)}."
        ) from None


def _target(request: Request, proposal_id: uuid.UUID) -> None:
    request.scope.setdefault("state", {})["audit_target"] = ("action_proposal", str(proposal_id))


async def _view(request: Request, ctx: AuthContext, proposal_id: uuid.UUID) -> ProposalOut:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        views = await proposals_for(
            s, user_id=ctx.user_id, approver_units=_approver_units(ctx), proposal_id=proposal_id
        )
    if not views:
        raise HTTPException(status_code=404, detail="Not found.")
    return _out(views[0], ctx, datetime.now(UTC))


@router.post(
    "/propose",
    status_code=201,
    response_model=ProposalOut,
    responses={
        200: {"description": "The same proposal again (same Idempotency-Key)"},
        403: {"description": "You have no role in that business unit"},
        409: {"description": "Idempotency-Key reused for a different proposal"},
        422: {"description": "Unknown tool or invalid inputs"},
    },
)
async def propose(request: Request, ctx: CurrentUser, body: ProposeIn) -> JSONResponse:
    if not can_propose(ctx, body.business_unit_id):
        raise HTTPException(status_code=403, detail="You can only propose in your own units.")
    inputs = _validated(body.tool, body.inputs)
    header = idempotency_key(request)
    # Scoped to the caller, so two people can never collide on a key.
    key = (
        hashlib.sha256(f"{ctx.user_id}:{header}".encode()).hexdigest()
        if header
        else uuid.uuid4().hex
    )
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            proposal_id = await add_proposal(
                s,
                organisation_id=ctx.organisation_id,
                business_unit_id=body.business_unit_id,
                tool=body.tool,
                inputs=inputs,
                derived_from={"channel": "web"},
                proposed_by=ctx.user_id,
                idempotency_key=key,
                expires_at=now + PROPOSAL_LIFETIME,
            )
        status_code = 201
    except IntegrityError:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            existing = await proposal_by_key(s, key)
        if (
            existing is None
            or existing.inputs != inputs
            or existing.business_unit_id != body.business_unit_id
            or existing.tool != body.tool
        ):
            raise HTTPException(
                status_code=409,
                detail="This Idempotency-Key was already used for a different proposal.",
            ) from None
        proposal_id, status_code = existing.id, 200
    _target(request, proposal_id)
    out = await _view(request, ctx, proposal_id)
    logger.info("action_proposed", proposal_id=str(proposal_id), tool=body.tool)
    return JSONResponse(status_code=status_code, content=out.model_dump(mode="json"))


@router.get("", response_model=list[ProposalOut])
async def list_proposals(
    request: Request, ctx: CurrentUser, status: ProposalStatus | None = None
) -> list[ProposalOut]:
    """Proposals in units you approve for, plus your own. Newest first."""
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        views = await proposals_for(
            s, user_id=ctx.user_id, approver_units=_approver_units(ctx), status=status
        )
    now = datetime.now(UTC)
    return [_out(v, ctx, now) for v in views]


@router.get(
    "/{proposal_id}",
    response_model=ProposalOut,
    responses={404: {"description": "Not found, or not yours to see"}},
)
async def get_proposal(request: Request, ctx: CurrentUser, proposal_id: uuid.UUID) -> ProposalOut:
    return await _view(request, ctx, proposal_id)


_DECIDE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "Not an approver of its unit, or your own proposal"},
    404: {"description": "Not found, or not yours to see"},
    409: {"description": "Already decided, or expired"},
    422: {"description": "Edited inputs are invalid"},
}


async def _decide(
    request: Request,
    ctx: AuthContext,
    proposal_id: uuid.UUID,
    *,
    approved: bool,
    edited: dict[str, Any] | None,  # Any: tool JSON
    reason: str,
) -> ProposalOut:
    now = datetime.now(UTC)
    expired = False
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        proposal = await lock_proposal(s, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Not found.")
        denial = approval_denial(
            ctx, business_unit_id=proposal.business_unit_id, proposed_by=proposal.proposed_by
        )
        if denial is ApprovalDenial.NOT_APPROVER:
            if proposal.proposed_by != ctx.user_id:
                # Someone else's proposal outside your units: indistinguishable from none.
                raise HTTPException(status_code=404, detail="Not found.")
            raise HTTPException(status_code=403, detail="Only an approver can decide this.")
        if denial is ApprovalDenial.OWN_PROPOSAL:
            raise HTTPException(
                status_code=403, detail="Another approver must decide your own proposal."
            )
        if proposal.status is not ProposalStatus.PENDING:
            raise HTTPException(status_code=409, detail="This proposal is already decided.")
        if proposal.expires_at <= now:
            proposal.status = ProposalStatus.EXPIRED  # committed, then refused below
            expired = True
        else:
            await record_decision(
                s,
                proposal,
                approver_id=ctx.user_id,
                approved=approved,
                # Validated only now, so only an entitled approver learns about the schema.
                edited_inputs=_validated(proposal.tool, edited) if edited is not None else None,
                reason=reason,
            )
    _target(request, proposal_id)
    if expired:
        raise HTTPException(status_code=409, detail="This proposal has expired.")
    if approved:
        await _enqueue(request, ctx.organisation_id, proposal_id)
    logger.info("action_decided", proposal_id=str(proposal_id), approved=approved)
    return await _view(request, ctx, proposal_id)


async def _enqueue(request: Request, organisation_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
    """The approval is already stored; a queue outage leaves it "approved" and is logged."""
    queue: JobQueue = request.app.state.job_queue
    try:
        await queue.enqueue_action(organisation_id, proposal_id)
    except (OSError, RedisError):
        logger.exception("action_enqueue_failed")


@router.post("/{proposal_id}/approve", response_model=ProposalOut, responses=_DECIDE_RESPONSES)
async def approve(
    request: Request, ctx: CurrentUser, proposal_id: uuid.UUID, body: ApproveIn
) -> ProposalOut:
    return await _decide(
        request, ctx, proposal_id, approved=True, edited=body.edited_inputs, reason=body.reason
    )


@router.post("/{proposal_id}/reject", response_model=ProposalOut, responses=_DECIDE_RESPONSES)
async def reject(
    request: Request, ctx: CurrentUser, proposal_id: uuid.UUID, body: RejectIn
) -> ProposalOut:
    return await _decide(request, ctx, proposal_id, approved=False, edited=None, reason=body.reason)
