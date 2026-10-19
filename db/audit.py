"""PostgreSQL audit sink: writes each AuditEvent in its own short transaction."""

from __future__ import annotations

import uuid

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.schema import AuditEventRecord
from db.tenancy import tenant_transaction
from lekha.audit import AuditEvent


class PostgresAuditSink:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: AuditEvent) -> None:
        # The ID is generated here and nothing is read back: INSERT ... RETURNING would also
        # require the row to pass the SELECT policy, which org-less events never do.
        statement = insert(AuditEventRecord).values(
            id=uuid.uuid4(),
            request_id=event.request_id,
            organisation_id=event.organisation_id,
            actor_user_id=event.actor_user_id,
            action=event.action,
            method=event.method,
            path=event.path,
            status_code=event.status_code,
            outcome=event.outcome,
            target_type=event.target_type,
            target_id=event.target_id,
        )
        if event.organisation_id is not None:
            async with tenant_transaction(self._session_factory, event.organisation_id) as s:
                await s.execute(statement)
        else:
            # Never signed in: no tenant scope. RLS allows inserting org-less events only.
            async with self._session_factory.begin() as s:
                await s.execute(statement)
