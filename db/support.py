"""Persistence for question requests. Callers run these inside ``tenant_transaction``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import AppUser, QueryLog, QuestionRequest, RequestStatus


async def asker_of(session: AsyncSession, query_id: uuid.UUID) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(QueryLog.user_id).where(QueryLog.id == query_id)
    )
    return found


async def log_request(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    query_id: uuid.UUID,
    user_id: uuid.UUID,
    business_unit_id: uuid.UUID,
    note: str,
) -> uuid.UUID:
    request_id = uuid.uuid4()
    session.add(
        QuestionRequest(
            id=request_id,
            organisation_id=organisation_id,
            query_log_id=query_id,
            user_id=user_id,
            business_unit_id=business_unit_id,
            note=note,
            status=RequestStatus.OPEN,
        )
    )
    await session.flush()  # a second request for the same question fails here
    return request_id


@dataclass(frozen=True, slots=True)
class RequestView:
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


async def requests_for(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    curator_units: Sequence[uuid.UUID],
    status: RequestStatus | None,
    limit: int = 200,
) -> list[RequestView]:
    """Requests in units the caller curates, plus the caller's own."""
    query = (
        select(QuestionRequest, QueryLog.question, AppUser.display_name)
        .join(QueryLog, QueryLog.id == QuestionRequest.query_log_id)
        .join(AppUser, AppUser.id == QuestionRequest.user_id)
        .where(
            or_(
                QuestionRequest.business_unit_id.in_(list(curator_units)),
                QuestionRequest.user_id == user_id,
            )
        )
        .order_by(QuestionRequest.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(QuestionRequest.status == status)
    rows = await session.execute(query)
    return [
        RequestView(
            r.id,
            question,
            name,
            r.business_unit_id,
            r.note,
            r.status,
            r.resolution,
            r.created_at,
            r.resolved_at,
            r.user_id == user_id,
        )
        for r, question, name in rows
    ]


async def lock_request(session: AsyncSession, request_id: uuid.UUID) -> QuestionRequest | None:
    found: QuestionRequest | None = await session.scalar(
        select(QuestionRequest).where(QuestionRequest.id == request_id).with_for_update()
    )
    return found
