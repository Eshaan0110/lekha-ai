"""Claiming, completing and releasing idempotency keys.

Each function runs in its own short tenant transaction, so a claim is visible to a
concurrent retry *before* the real work starts. Row locks serialise two requests racing on
the same key.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.schema import IdempotencyRecord, IdempotencyState
from db.tenancy import tenant_transaction


class ClaimOutcome(StrEnum):
    NEW = "new"  # proceed with the work, then complete or release
    REPLAY = "replay"  # same request already done: return the stored response
    MISMATCH = "mismatch"  # key reused for a different request
    IN_PROGRESS = "in_progress"  # the first request with this key is still running


@dataclass(frozen=True, slots=True)
class Claim:
    outcome: ClaimOutcome
    status: int | None = None
    body: dict[str, Any] | None = None  # Any: a stored JSON response
    location: str | None = None


@dataclass(frozen=True, slots=True)
class KeyScope:
    organisation_id: uuid.UUID
    user_id: uuid.UUID
    key: str


class IdempotencyStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ttl: timedelta,
        stale_after: timedelta,
    ) -> None:
        self._factory = session_factory
        self._ttl = ttl
        self._stale_after = stale_after

    async def claim(
        self, scope: KeyScope, *, operation: str, fingerprint: bytes, now: datetime
    ) -> Claim:
        async with tenant_transaction(self._factory, scope.organisation_id) as s:
            inserted = await s.scalar(
                insert(IdempotencyRecord)
                .values(
                    organisation_id=scope.organisation_id,
                    user_id=scope.user_id,
                    key=scope.key,
                    operation=operation,
                    request_fingerprint=fingerprint,
                    state=IdempotencyState.IN_PROGRESS,
                    expires_at=now + self._ttl,
                )
                .on_conflict_do_nothing(constraint="uq_idempotency_record_user_key")
                .returning(IdempotencyRecord.id)
            )
            if inserted is not None:
                return Claim(ClaimOutcome.NEW)

            record = (
                await s.execute(
                    select(IdempotencyRecord)
                    .where(*_matches(scope))
                    .with_for_update()  # serialise concurrent retries of the same key
                )
            ).scalar_one()
            same_request = (
                record.operation == operation and record.request_fingerprint == fingerprint
            )
            expired = record.expires_at <= now
            abandoned = (
                record.state is IdempotencyState.IN_PROGRESS
                and record.updated_at <= now - self._stale_after
            )
            if expired or (same_request and abandoned):
                # Start over: an expired key is free again, and an in-progress claim that
                # stopped making progress (e.g. the server crashed) may be taken over.
                await s.execute(
                    update(IdempotencyRecord)
                    .where(IdempotencyRecord.id == record.id)
                    .values(
                        operation=operation,
                        request_fingerprint=fingerprint,
                        state=IdempotencyState.IN_PROGRESS,
                        response_status=None,
                        response_body=None,
                        response_location=None,
                        expires_at=now + self._ttl,
                        updated_at=now,
                    )
                )
                return Claim(ClaimOutcome.NEW)
            if not same_request:
                return Claim(ClaimOutcome.MISMATCH)
            if record.state is IdempotencyState.IN_PROGRESS:
                return Claim(ClaimOutcome.IN_PROGRESS)
            return Claim(
                ClaimOutcome.REPLAY,
                status=record.response_status,
                body=record.response_body,
                location=record.response_location,
            )

    async def complete(
        self, scope: KeyScope, *, status: int, body: dict[str, Any], location: str | None
    ) -> None:
        async with tenant_transaction(self._factory, scope.organisation_id) as s:
            await s.execute(
                update(IdempotencyRecord)
                .where(*_matches(scope), IdempotencyRecord.state == IdempotencyState.IN_PROGRESS)
                .values(
                    state=IdempotencyState.COMPLETED,
                    response_status=status,
                    response_body=body,
                    response_location=location,
                )
            )

    async def release(self, scope: KeyScope) -> None:
        """Forget an in-progress claim whose work failed, so the client can retry."""
        async with tenant_transaction(self._factory, scope.organisation_id) as s:
            await s.execute(
                delete(IdempotencyRecord).where(
                    *_matches(scope), IdempotencyRecord.state == IdempotencyState.IN_PROGRESS
                )
            )


def _matches(scope: KeyScope) -> tuple[Any, ...]:  # Any: SQLAlchemy column expressions
    return (
        IdempotencyRecord.organisation_id == scope.organisation_id,
        IdempotencyRecord.user_id == scope.user_id,
        IdempotencyRecord.key == scope.key,
    )
