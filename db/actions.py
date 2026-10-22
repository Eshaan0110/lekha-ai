"""Persistence for actions (proposals, approvals, executions) and the SIMULATED ticketing
system. Callers run the functions inside ``tenant_transaction``; ``SimulatedTicketing``
opens its own, as a separate outside system would."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from db.schema import (
    ActionApproval,
    ActionExecution,
    ActionProposal,
    AppUser,
    SimulatedTicket,
)
from db.tenancy import tenant_transaction
from lekha.actions import ProposalStatus, TicketInputs


async def add_proposal(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    business_unit_id: uuid.UUID,
    tool: str,
    inputs: dict[str, Any],  # Any: validated tool JSON
    derived_from: dict[str, Any],  # Any: provenance JSON
    proposed_by: uuid.UUID,
    idempotency_key: str,
    expires_at: datetime,
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    session.add(
        ActionProposal(
            id=proposal_id,
            organisation_id=organisation_id,
            business_unit_id=business_unit_id,
            tool=tool,
            inputs=inputs,
            derived_from=derived_from,
            proposed_by=proposed_by,
            idempotency_key=idempotency_key,
            status=ProposalStatus.PENDING,
            expires_at=expires_at,
        )
    )
    await session.flush()  # a reused idempotency key fails here
    return proposal_id


async def proposal_by_key(session: AsyncSession, idempotency_key: str) -> ActionProposal | None:
    found: ActionProposal | None = await session.scalar(
        select(ActionProposal).where(ActionProposal.idempotency_key == idempotency_key)
    )
    return found


async def lock_proposal(session: AsyncSession, proposal_id: uuid.UUID) -> ActionProposal | None:
    found: ActionProposal | None = await session.scalar(
        select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
    )
    return found


async def record_decision(
    session: AsyncSession,
    proposal: ActionProposal,
    *,
    approver_id: uuid.UUID,
    approved: bool,
    edited_inputs: dict[str, Any] | None,  # Any: validated tool JSON
    reason: str,
) -> None:
    """Store the decision and move the (locked) proposal on. One decision per proposal."""
    session.add(
        ActionApproval(
            organisation_id=proposal.organisation_id,
            proposal_id=proposal.id,
            approver_id=approver_id,
            approved=approved,
            edited_inputs=edited_inputs,
            reason=reason,
        )
    )
    proposal.status = ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
    await session.flush()


@dataclass(frozen=True, slots=True)
class ProposalView:
    id: uuid.UUID
    business_unit_id: uuid.UUID
    tool: str
    inputs: dict[str, Any]  # Any: tool JSON (the edited inputs, once approved with edits)
    proposed_by: str
    mine: bool
    status: ProposalStatus
    created_at: datetime
    expires_at: datetime
    decided_by: str | None
    reason: str | None
    external_id: str | None
    error_code: str | None


async def proposals_for(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    approver_units: Sequence[uuid.UUID],
    status: ProposalStatus | None = None,
    proposal_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[ProposalView]:
    """Proposals in units the caller approves for, plus the caller's own. Newest first."""
    proposer = aliased(AppUser)
    approver = aliased(AppUser)
    query = (
        select(ActionProposal, proposer.display_name, ActionApproval, approver.display_name)
        .add_columns(ActionExecution.external_id, ActionExecution.error_code)
        .join(proposer, proposer.id == ActionProposal.proposed_by)
        .outerjoin(ActionApproval, ActionApproval.proposal_id == ActionProposal.id)
        .outerjoin(approver, approver.id == ActionApproval.approver_id)
        .outerjoin(ActionExecution, ActionExecution.proposal_id == ActionProposal.id)
        .where(
            or_(
                ActionProposal.business_unit_id.in_(list(approver_units)),
                ActionProposal.proposed_by == user_id,
            )
        )
        .order_by(ActionProposal.created_at.desc(), ActionProposal.id)
        .limit(limit)
    )
    if status is not None:
        query = query.where(ActionProposal.status == status)
    if proposal_id is not None:
        query = query.where(ActionProposal.id == proposal_id)
    rows = await session.execute(query)
    return [
        ProposalView(
            id=p.id,
            business_unit_id=p.business_unit_id,
            tool=p.tool,
            inputs=(a.edited_inputs if a is not None and a.edited_inputs else p.inputs),
            proposed_by=proposer_name,
            mine=p.proposed_by == user_id,
            status=p.status,
            created_at=p.created_at,
            expires_at=p.expires_at,
            decided_by=approver_name,
            reason=a.reason if a is not None else None,
            external_id=external_id,
            error_code=error_code,
        )
        for p, proposer_name, a, approver_name, external_id, error_code in rows
    ]


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    tool: str
    inputs: dict[str, Any]  # Any: tool JSON as approved
    idempotency_key: str
    status: ProposalStatus
    already_executed: bool


async def execution_target(session: AsyncSession, proposal_id: uuid.UUID) -> ExecutionTarget | None:
    row = (
        await session.execute(
            select(ActionProposal, ActionApproval.edited_inputs, ActionExecution.id)
            .outerjoin(ActionApproval, ActionApproval.proposal_id == ActionProposal.id)
            .outerjoin(ActionExecution, ActionExecution.proposal_id == ActionProposal.id)
            .where(ActionProposal.id == proposal_id)
        )
    ).first()
    if row is None:
        return None
    proposal, edited, execution_id = row
    return ExecutionTarget(
        tool=proposal.tool,
        inputs=edited or proposal.inputs,
        idempotency_key=proposal.idempotency_key,
        status=proposal.status,
        already_executed=execution_id is not None,
    )


async def record_execution(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    succeeded: bool,
    external_id: str | None,
    error_code: str | None,
    attempts: int,
    now: datetime,
) -> bool:
    """Record the one execution; False if another worker recorded it first."""
    proposal = await lock_proposal(session, proposal_id)
    if proposal is None or proposal.status is not ProposalStatus.APPROVED:
        return False
    session.add(
        ActionExecution(
            organisation_id=proposal.organisation_id,
            proposal_id=proposal_id,
            idempotency_key=proposal.idempotency_key,
            succeeded=succeeded,
            external_id=external_id,
            error_code=error_code,
            attempts=attempts,
            finished_at=now,
        )
    )
    proposal.status = ProposalStatus.EXECUTED if succeeded else ProposalStatus.FAILED
    await session.flush()
    return True


class SimulatedTicketing:
    """SIMULATION ONLY (owner decision D4): a fake HR/IT ticketing system in a local table.
    Honours the interface's contract: the same idempotency key always returns the same
    "SIM-" reference and never creates a second ticket."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_ticket(
        self, organisation_id: uuid.UUID, idempotency_key: str, ticket: TicketInputs
    ) -> str:
        async with tenant_transaction(self._session_factory, organisation_id) as s:
            await s.execute(
                insert(SimulatedTicket)
                .values(
                    organisation_id=organisation_id,
                    reference=f"SIM-{secrets.token_hex(4).upper()}",
                    idempotency_key=idempotency_key,
                    title=ticket.title,
                    category=ticket.category.value,
                    priority=ticket.priority.value,
                    description=ticket.description,
                )
                .on_conflict_do_nothing(constraint="uq_simulated_ticket_key")
            )
            reference: str = (
                await s.execute(
                    select(SimulatedTicket.reference).where(
                        SimulatedTicket.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
        return reference
