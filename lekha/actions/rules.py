"""Who may propose and who may approve an action. Pure: no I/O.

- Anyone with a role in a business unit may propose an action there (for themselves).
- Only a holder of the ``approver`` role in that unit may approve or reject it, and never
  their own proposal (separation of duties, plan threat model "unauthorised actions").
- A proposal expires if nobody decides within PROPOSAL_LIFETIME.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from enum import StrEnum

from lekha.auth import AuthContext

APPROVER_ROLE = "approver"
PROPOSAL_LIFETIME = timedelta(hours=24)


class ProposalStatus(StrEnum):
    PENDING = "pending_approval"
    APPROVED = "approved"  # waiting for (or during) execution
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


class ApprovalDenial(StrEnum):
    NOT_APPROVER = "not_approver"
    OWN_PROPOSAL = "own_proposal"


def can_propose(ctx: AuthContext, business_unit_id: uuid.UUID) -> bool:
    return bool(ctx.roles_by_bu.get(business_unit_id))


def approval_denial(
    ctx: AuthContext, *, business_unit_id: uuid.UUID, proposed_by: uuid.UUID
) -> ApprovalDenial | None:
    if APPROVER_ROLE not in ctx.roles_by_bu.get(business_unit_id, frozenset()):
        return ApprovalDenial.NOT_APPROVER
    if proposed_by == ctx.user_id:
        return ApprovalDenial.OWN_PROPOSAL
    return None
