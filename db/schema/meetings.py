"""Meetings (Phase 4, meeting memory).

A meeting's transcript is stored and searched as a document of kind "transcript", so it
inherits versions, permissions, deletion, citations and audit unchanged. This table holds
what is specific to meetings: when it started, who organised it, who took part, and the
source identifiers used to recognise the same meeting imported twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped


class Meeting(TenantScoped, Base):
    __tablename__ = "meeting"

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    organiser_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # vtt | srt | txt
    external_id: Mapped[str | None] = mapped_column(Text)  # the source system's meeting ID

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_meeting_org_id"),
        UniqueConstraint("document_id", name="uq_meeting_document"),
        UniqueConstraint("organisation_id", "source", "external_id", name="uq_meeting_external"),
        ForeignKeyConstraint(
            ["organisation_id", "document_id"], ["document.organisation_id", "document.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "organiser_user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("source IN ('vtt', 'srt', 'txt')", name="source_known"),
        CheckConstraint(
            "external_id IS NULL OR char_length(external_id) BETWEEN 1 AND 200",
            name="external_id_length",
        ),
        Index("ix_meeting_organiser_started", "organiser_user_id", "started_at"),
    )


class MeetingParticipant(TenantScoped, Base):
    """A Lekha user who took part. Participants are the meeting's guest list."""

    __tablename__ = "meeting_participant"

    meeting_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    __table_args__ = (
        UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant"),
        ForeignKeyConstraint(
            ["organisation_id", "meeting_id"], ["meeting.organisation_id", "meeting.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
    )
