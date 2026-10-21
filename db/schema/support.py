"""Pilot support (Phase 4): questions people chose to log as requests for their curators."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

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
from db.schema._common import TenantScoped, pg_enum


class RequestStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"  # e.g. the missing policy was uploaded
    DISMISSED = "dismissed"


class QuestionRequest(TenantScoped, Base):
    """A question Lekha couldn't answer, logged by the person who asked it (their choice),
    for the curators of one of their business units to follow up."""

    __tablename__ = "question_request"

    query_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[RequestStatus] = mapped_column(
        pg_enum(RequestStatus, "request_status"), nullable=False
    )
    resolution: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("query_log_id", name="uq_question_request_query"),
        ForeignKeyConstraint(
            ["organisation_id", "query_log_id"], ["query_log.organisation_id", "query_log.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_question_request_asker",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "resolved_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_question_request_resolver",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        CheckConstraint("char_length(note) <= 500", name="note_length"),
        CheckConstraint("char_length(resolution) <= 500", name="resolution_length"),
        CheckConstraint("(status = 'open') = (resolved_at IS NULL)", name="resolved_unless_open"),
        Index("ix_question_request_unit_status", "business_unit_id", "status"),
    )
