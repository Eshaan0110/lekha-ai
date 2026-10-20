"""Deletion requests: who asked to delete what, and what the purge removed (plan data model)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum


class DeletionStatus(StrEnum):
    PENDING = "pending"  # document hidden; derived data still being purged
    COMPLETED = "completed"
    FAILED = "failed"


class DeletionRequest(TenantScoped, Base):
    """One request to delete a document. The document row stays as a tombstone (with
    ``deleted_at``) so audit events that name it still resolve; its chunks, vectors and
    stored files are purged."""

    __tablename__ = "deletion_request"

    scope: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[DeletionStatus] = mapped_column(
        pg_enum(DeletionStatus, "deletion_status"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    chunks_removed: Mapped[int | None] = mapped_column()
    files_removed: Mapped[int | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_deletion_request_org_id"),
        # One request per document: a second request for a deleted document is pointless.
        UniqueConstraint("organisation_id", "document_id", name="uq_deletion_request_document"),
        ForeignKeyConstraint(
            ["organisation_id", "document_id"], ["document.organisation_id", "document.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "requested_by"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("scope IN ('document')", name="scope_known"),
        CheckConstraint("error_code IS NULL OR error_code ~ '^[a-z_]{1,60}$'", name="error_code"),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)", name="completed_has_time"
        ),
    )
