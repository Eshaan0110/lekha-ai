"""Idempotency records: remember the outcome of a write so a retried request can replay it."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum


class IdempotencyState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyRecord(TenantScoped, Base):
    __tablename__ = "idempotency_record"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "POST /api/v1/documents"
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[IdempotencyState] = mapped_column(
        pg_enum(IdempotencyState, "idempotency_state"), nullable=False
    )
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_location: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Keys are scoped to one user: users can never see or collide with each other's.
        UniqueConstraint(
            "organisation_id", "user_id", "key", name="uq_idempotency_record_user_key"
        ),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("key ~ '^[A-Za-z0-9._:-]{8,255}$'", name="key_format"),
        CheckConstraint("octet_length(request_fingerprint) = 32", name="fingerprint_length"),
        CheckConstraint(
            "(state = 'completed') = (response_status IS NOT NULL AND response_body IS NOT NULL)",
            name="completed_has_response",
        ),
        Index("ix_idempotency_record_expires_at", "expires_at"),
    )
