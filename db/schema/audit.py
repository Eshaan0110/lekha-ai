"""The append-only audit log table.

Deliberately has no foreign keys: an audit record must outlive the user, document, or even
organisation it mentions (deletions and retention must never erase the trail). Updates and
deletes are blocked by grants and by a trigger (migration 0003).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, SmallInteger, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import UUID_DEFAULT, pg_enum
from lekha.audit import MAX_PATH_LENGTH, Outcome


class AuditEventRecord(Base):
    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=UUID_DEFAULT)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    outcome: Mapped[Outcome] = mapped_column(pg_enum(Outcome, "audit_outcome"), nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        CheckConstraint("char_length(request_id) BETWEEN 1 AND 64", name="request_id_length"),
        CheckConstraint("char_length(action) BETWEEN 1 AND 300", name="action_length"),
        CheckConstraint("method ~ '^[A-Z]{3,7}$'", name="method_format"),
        CheckConstraint(f"char_length(path) <= {MAX_PATH_LENGTH}", name="path_length"),
        CheckConstraint("status_code BETWEEN 100 AND 599", name="status_code_range"),
        CheckConstraint(
            "target_type IS NULL OR target_type ~ '^[a-z_]{1,40}$'", name="target_type_format"
        ),
        # An actor always belongs to an organisation.
        CheckConstraint(
            "actor_user_id IS NULL OR organisation_id IS NOT NULL", name="actor_has_organisation"
        ),
        # BRIN suits an append-only, time-ordered table (plan: "BRIN on created_at").
        Index("ix_audit_event_occurred_at", "occurred_at", postgresql_using="brin"),
        Index("ix_audit_event_org_occurred", "organisation_id", "occurred_at"),
        Index("ix_audit_event_request_id", "request_id"),
        Index("ix_audit_event_actor", "actor_user_id"),
    )
