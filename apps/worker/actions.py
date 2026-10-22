"""Execute an approved action exactly once (plan "Safe Actions": propose → approve → execute).

Exactly once comes from two guards:
- the ticketing system honours the proposal's idempotency key, so a retry after a crash
  between "ticket created" and "execution recorded" returns the same ticket;
- ``record_execution`` locks the proposal and refuses a second record.

Only transient failures (the system unreachable) are retried; a refusal is final.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.pipeline import TransientJobError
from db.actions import execution_target, record_execution
from db.tenancy import tenant_transaction
from lekha.actions import ProposalStatus, TicketInputs
from lekha.connectors import TicketingSystem, TicketingUnavailableError, TicketRejectedError

logger = structlog.get_logger("lekha.actions")


@dataclass(frozen=True, slots=True)
class ActionDeps:
    session_factory: async_sessionmaker[AsyncSession]
    ticketing: TicketingSystem


async def run_action(
    deps: ActionDeps,
    organisation_id: uuid.UUID,
    proposal_id: uuid.UUID,
    *,
    attempt: int = 1,
    final_attempt: bool = True,
) -> ProposalStatus | None:
    async with tenant_transaction(deps.session_factory, organisation_id) as s:
        target = await execution_target(s, proposal_id)
    if target is None:
        logger.warning("action_not_found", proposal_id=str(proposal_id))
        return None
    if target.status is not ProposalStatus.APPROVED or target.already_executed:
        return target.status  # never approved, or already done: nothing to do
    structlog.contextvars.bind_contextvars(proposal_id=str(proposal_id))

    external_id: str | None = None
    error_code: str | None = None
    try:
        ticket = TicketInputs.model_validate(target.inputs)
        external_id = await deps.ticketing.create_ticket(
            organisation_id, target.idempotency_key, ticket
        )
    except ValidationError:
        error_code = "invalid_inputs"  # cannot happen for stored inputs; refuse, never guess
    except TicketRejectedError:
        error_code = "rejected_by_system"
    except TicketingUnavailableError:
        if not final_attempt:
            logger.warning("action_execution_retry", attempt=attempt)
            raise TransientJobError("ticketing_unavailable") from None
        error_code = "ticketing_unavailable"

    async with tenant_transaction(deps.session_factory, organisation_id) as s:
        recorded = await record_execution(
            s,
            proposal_id,
            succeeded=error_code is None,
            external_id=external_id,
            error_code=error_code,
            attempts=attempt,
            now=datetime.now(UTC),
        )
    status = ProposalStatus.EXECUTED if error_code is None else ProposalStatus.FAILED
    logger.info(
        "action_executed" if error_code is None else "action_failed",
        error_code=error_code,
        recorded=recorded,
    )
    return status
