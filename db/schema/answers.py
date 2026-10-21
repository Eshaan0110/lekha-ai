"""Question and answer logs (plan data model: query_log, answer_log, citation).

A citation is a tuple (answer, chunk, document version), never a bare snippet, so it can
always be resolved back to its source. ``chunk_id`` has no foreign key on purpose: chunks
are replaced when a version is re-indexed and purged on deletion, while the citation row
must stay resolvable to its version (or be removed by the deletion workflow).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped


class QueryLog(TenantScoped, Base):
    """What a user asked. Written before any retrieval work (plan)."""

    __tablename__ = "query_log"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_query_log_org_id"),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("char_length(question) BETWEEN 1 AND 1000", name="question_length"),
        CheckConstraint("language IN ('en', 'hi', 'kn', 'und')", name="language_known"),
    )


class AnswerLog(TenantScoped, Base):
    """What Lekha replied: an answer with citations, or an abstention and its reason."""

    __tablename__ = "answer_log"

    query_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)  # None when it abstained before asking one
    prompt_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    answer: Mapped[str | None] = mapped_column(Text)  # None when abstained or redacted
    abstention_reason: Mapped[str | None] = mapped_column(Text)
    candidate_count: Mapped[int] = mapped_column(nullable=False)
    dropped_citations: Mapped[int] = mapped_column(nullable=False, server_default="0")
    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    # Set when a cited document is deleted: the answer text is wiped (plan: derived data
    # is invalidated on deletion) but the row stays, so the audit trail still resolves.
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_answer_log_org_id"),
        UniqueConstraint("query_log_id", name="uq_answer_log_query"),
        ForeignKeyConstraint(
            ["organisation_id", "query_log_id"], ["query_log.organisation_id", "query_log.id"]
        ),
        CheckConstraint(
            "abstention_reason IS NULL OR abstention_reason ~ '^[a-z_]{1,60}$'",
            name="abstention_reason_format",
        ),
        CheckConstraint(
            "prompt_sha256 IS NULL OR octet_length(prompt_sha256) = 32", name="prompt_sha256_length"
        ),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
    )


class Citation(TenantScoped, Base):
    __tablename__ = "citation"

    answer_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_number: Mapped[int] = mapped_column(nullable=False)
    span: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "answer_log_id"], ["answer_log.organisation_id", "answer_log.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
        ),
        CheckConstraint("source_number > 0", name="source_number_positive"),
    )


class AnswerFeedback(TenantScoped, Base):
    """Was this answer helpful? One verdict per question, from the person who asked it."""

    __tablename__ = "answer_feedback"

    query_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    helpful: Mapped[bool] = mapped_column(nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("query_log_id", name="uq_answer_feedback_query"),
        ForeignKeyConstraint(
            ["organisation_id", "query_log_id"], ["query_log.organisation_id", "query_log.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("comment IS NULL OR char_length(comment) <= 1000", name="comment_length"),
    )
