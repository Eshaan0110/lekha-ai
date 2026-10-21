"""Reading the audit trail (``audit_reader`` role). Callers run this inside
``tenant_transaction``; row-level security already limits rows to the caller's organisation.

Scope: events whose actor holds (or held) a role in one of the reader's audit units. The
log is append-only; this module only reads it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import AppUser, AuditEventRecord, UserRoleGrant


@dataclass(frozen=True, slots=True)
class AuditRow:
    id: uuid.UUID
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


async def audit_page(
    session: AsyncSession,
    *,
    units: Sequence[uuid.UUID],
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[AuditRow]:
    """Newest first; ``after`` is the (time, id) of the last row of the previous page."""
    in_scope = exists().where(
        UserRoleGrant.user_id == AuditEventRecord.actor_user_id,
        UserRoleGrant.business_unit_id.in_(list(units)),
    )
    query = (
        select(AuditEventRecord, AppUser.display_name)
        .outerjoin(AppUser, AppUser.id == AuditEventRecord.actor_user_id)
        .where(in_scope)
        .order_by(AuditEventRecord.occurred_at.desc(), AuditEventRecord.id.desc())
        .limit(limit)
    )
    if after is not None:
        when, ident = after
        query = query.where(
            or_(
                AuditEventRecord.occurred_at < when,
                and_(AuditEventRecord.occurred_at == when, AuditEventRecord.id < ident),
            )
        )
    rows = await session.execute(query)
    return [
        AuditRow(
            e.id,
            e.occurred_at,
            name,
            e.action,
            e.method,
            e.path,
            e.status_code,
            e.outcome.value,
            e.target_type,
            e.target_id,
            e.request_id,
        )
        for e, name in rows
    ]
