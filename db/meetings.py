"""Persistence for meetings. Callers run these inside ``tenant_transaction``.

Reading a meeting always goes through its document and the standard access rule
(``readable_condition``): a meeting is visible exactly when its transcript document is.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.access import readable_condition
from db.schema import (
    AppUser,
    Document,
    DocumentFile,
    DocumentVersion,
    IngestionJob,
    Meeting,
    MeetingMinutes,
    MeetingParticipant,
    MinutesStatus,
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
    organiser_user_id: uuid.UUID
    version_id: uuid.UUID  # the current transcript version


_MeetingRow = tuple[uuid.UUID, uuid.UUID, str, datetime, int, str, uuid.UUID, uuid.UUID]


def _readable_meetings(ctx: AuthContext, now: datetime) -> Select[_MeetingRow]:
    return (
        select(
            Meeting.id,
            Document.id,
            Document.title,
            Meeting.started_at,
            DocumentVersion.version_number,
            DocumentFile.storage_key,
            Meeting.organiser_user_id,
            DocumentVersion.id,
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
    return MeetingDetail(summary, row[5], row[6], row[7])


async def meeting_organiser(session: AsyncSession, document_id: uuid.UUID) -> uuid.UUID | None:
    """The organiser of the meeting stored as this document, if it is a meeting."""
    found: uuid.UUID | None = await session.scalar(
        select(Meeting.organiser_user_id).where(Meeting.document_id == document_id)
    )
    return found


# --- minutes -------------------------------------------------------------------------


async def record_minutes(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    meeting_id: uuid.UUID,
    transcript_version_id: uuid.UUID,
    template: str,
    summary: str,
    decisions: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    dropped_items: int,
    model: str,
    drafted_by: uuid.UUID,
) -> MeetingMinutes:
    minutes = MeetingMinutes(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        meeting_id=meeting_id,
        transcript_version_id=transcript_version_id,
        status=MinutesStatus.DRAFT,
        template=template,
        summary=summary,
        decisions=decisions,
        action_items=action_items,
        dropped_items=dropped_items,
        model=model,
        drafted_by=drafted_by,
    )
    session.add(minutes)
    await session.flush()
    return minutes


async def minutes_of(session: AsyncSession, meeting_id: uuid.UUID) -> list[MeetingMinutes]:
    rows = await session.execute(
        select(MeetingMinutes)
        .where(MeetingMinutes.meeting_id == meeting_id)
        .order_by(MeetingMinutes.created_at.desc())
    )
    return list(rows.scalars())


async def lock_minutes(session: AsyncSession, minutes_id: uuid.UUID) -> MeetingMinutes | None:
    found: MeetingMinutes | None = await session.scalar(
        select(MeetingMinutes).where(MeetingMinutes.id == minutes_id).with_for_update()
    )
    return found


async def approve_minutes(
    session: AsyncSession,
    minutes: MeetingMinutes,
    *,
    decided_by: uuid.UUID,
    now: datetime,
    edited: dict[str, Any] | None,
) -> None:
    """Approve one draft; any earlier approval for the same transcript is superseded."""
    await session.execute(
        update(MeetingMinutes)
        .where(
            MeetingMinutes.transcript_version_id == minutes.transcript_version_id,
            MeetingMinutes.status == MinutesStatus.APPROVED,
        )
        .values(status=MinutesStatus.SUPERSEDED)
    )
    if edited is not None:
        minutes.summary = edited["summary"]
        minutes.decisions = edited["decisions"]
        minutes.action_items = edited["action_items"]
        minutes.edited = True
    minutes.status = MinutesStatus.APPROVED
    minutes.decided_by = decided_by
    minutes.decided_at = now


async def reject_minutes(
    session: AsyncSession, minutes: MeetingMinutes, *, decided_by: uuid.UUID, now: datetime
) -> None:
    minutes.status = MinutesStatus.REJECTED
    minutes.decided_by = decided_by
    minutes.decided_at = now


async def approved_minutes_for_version(
    session: AsyncSession, version_id: uuid.UUID
) -> MeetingMinutes | None:
    """The approved minutes of this transcript version, which are indexed with it."""
    found: MeetingMinutes | None = await session.scalar(
        select(MeetingMinutes).where(
            MeetingMinutes.transcript_version_id == version_id,
            MeetingMinutes.status == MinutesStatus.APPROVED,
        )
    )
    return found


async def ingestion_job_of(session: AsyncSession, version_id: uuid.UUID) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(IngestionJob.id).where(IngestionJob.document_version_id == version_id)
    )
    return found


async def delete_minutes_of_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    """Deletion workflow: minutes are derived from the transcript and go with it."""
    meeting_ids = select(Meeting.id).where(Meeting.document_id == document_id)
    result = await session.execute(
        delete(MeetingMinutes).where(MeetingMinutes.meeting_id.in_(meeting_ids))
    )
    return int(getattr(result, "rowcount", 0) or 0)
