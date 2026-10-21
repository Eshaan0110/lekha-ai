"""Persistence for meetings. Callers run these inside ``tenant_transaction``.

Reading a meeting always goes through its document and the standard access rule
(``readable_condition``): a meeting is visible exactly when its transcript document is.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.access import readable_condition
from db.schema import (
    AppUser,
    Document,
    DocumentFile,
    DocumentVersion,
    Meeting,
    MeetingParticipant,
)
from lekha.auth import AuthContext

SAME_MEETING_WINDOW = timedelta(minutes=2)  # plan: organiser + start time within 2 minutes


@dataclass(frozen=True, slots=True)
class KnownMeeting:
    meeting_id: uuid.UUID
    canonical_key: str


async def find_same_meeting(
    session: AsyncSession,
    *,
    source: str,
    external_id: str | None,
    organiser_user_id: uuid.UUID,
    started_at: datetime,
) -> KnownMeeting | None:
    """The meeting this import is another copy of: same source ID first, else the same
    organiser starting within two minutes (plan dedup rules)."""
    query = select(Meeting.id, Document.canonical_key).join(
        Document, Document.id == Meeting.document_id
    )
    if external_id:
        row = (
            await session.execute(
                query.where(Meeting.source == source, Meeting.external_id == external_id)
            )
        ).first()
        if row is not None:
            return KnownMeeting(row[0], row[1])
    row = (
        await session.execute(
            query.where(
                Meeting.organiser_user_id == organiser_user_id,
                Meeting.started_at.between(
                    started_at - SAME_MEETING_WINDOW, started_at + SAME_MEETING_WINDOW
                ),
            )
            .order_by(func.abs(func.extract("epoch", Meeting.started_at - started_at)))
            .limit(1)
        )
    ).first()
    return KnownMeeting(row[0], row[1]) if row is not None else None


async def record_meeting(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    meeting_id: uuid.UUID,
    document_id: uuid.UUID,
    started_at: datetime,
    organiser_user_id: uuid.UUID,
    source: str,
    external_id: str | None,
) -> None:
    session.add(
        Meeting(
            id=meeting_id,
            organisation_id=organisation_id,
            document_id=document_id,
            started_at=started_at,
            organiser_user_id=organiser_user_id,
            source=source,
            external_id=external_id,
        )
    )
    await session.flush()


async def set_participants(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    meeting_id: uuid.UUID,
    user_ids: Sequence[uuid.UUID],
) -> None:
    """The latest import decides who took part (and therefore who may read)."""
    await session.execute(
        delete(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)
    )
    session.add_all(
        MeetingParticipant(organisation_id=organisation_id, meeting_id=meeting_id, user_id=u)
        for u in sorted(set(user_ids))
    )


async def existing_user_ids(session: AsyncSession, user_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    rows = await session.execute(
        select(AppUser.id).where(AppUser.id.in_(list(user_ids)), AppUser.is_active.is_(True))
    )
    return set(rows.scalars())


@dataclass(frozen=True, slots=True)
class MeetingSummary:
    meeting_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    started_at: datetime
    version_number: int
    participants: tuple[str, ...]  # display names


@dataclass(frozen=True, slots=True)
class MeetingDetail:
    summary: MeetingSummary
    storage_key: str


_MeetingRow = tuple[uuid.UUID, uuid.UUID, str, datetime, int, str]


def _readable_meetings(ctx: AuthContext, now: datetime) -> Select[_MeetingRow]:
    return (
        select(
            Meeting.id,
            Document.id,
            Document.title,
            Meeting.started_at,
            DocumentVersion.version_number,
            DocumentFile.storage_key,
        )
        .join(Document, Document.id == Meeting.document_id)
        .join(
            DocumentVersion,
            and_(
                DocumentVersion.id == Document.current_version_id,
                DocumentVersion.document_id == Document.id,
            ),
        )
        .join(DocumentFile, DocumentFile.id == DocumentVersion.file_id)
        .where(readable_condition(ctx, now=now))
    )


async def _names(
    session: AsyncSession, meeting_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    rows = await session.execute(
        select(MeetingParticipant.meeting_id, AppUser.display_name)
        .join(AppUser, AppUser.id == MeetingParticipant.user_id)
        .where(MeetingParticipant.meeting_id.in_(list(meeting_ids)))
        .order_by(AppUser.display_name)
    )
    names: dict[uuid.UUID, list[str]] = {}
    for meeting_id, name in rows:
        names.setdefault(meeting_id, []).append(name)
    return names


async def list_readable_meetings(
    session: AsyncSession, ctx: AuthContext, *, now: datetime, limit: int = 200
) -> list[MeetingSummary]:
    rows = (
        await session.execute(
            _readable_meetings(ctx, now).order_by(Meeting.started_at.desc()).limit(limit)
        )
    ).all()
    names = await _names(session, [r[0] for r in rows])
    return [MeetingSummary(r[0], r[1], r[2], r[3], r[4], tuple(names.get(r[0], []))) for r in rows]


async def readable_meeting(
    session: AsyncSession, ctx: AuthContext, meeting_id: uuid.UUID, *, now: datetime
) -> MeetingDetail | None:
    row = (
        await session.execute(_readable_meetings(ctx, now).where(Meeting.id == meeting_id))
    ).first()
    if row is None:
        return None
    names = await _names(session, [row[0]])
    summary = MeetingSummary(row[0], row[1], row[2], row[3], row[4], tuple(names.get(row[0], [])))
    return MeetingDetail(summary, row[5])


async def meeting_organiser(session: AsyncSession, document_id: uuid.UUID) -> uuid.UUID | None:
    """The organiser of the meeting stored as this document, if it is a meeting."""
    found: uuid.UUID | None = await session.scalar(
        select(Meeting.organiser_user_id).where(Meeting.document_id == document_id)
    )
    return found
