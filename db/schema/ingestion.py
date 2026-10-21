"""Ingestion jobs (one per document version) and the chunks they produce."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum


class IngestionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IngestionJob(TenantScoped, Base):
    __tablename__ = "ingestion_job"

    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[IngestionStatus] = mapped_column(
        pg_enum(IngestionStatus, "ingestion_status"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(Text)  # a code, never document text
    chunk_count: Mapped[int | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_ingestion_job_org_id"),
        UniqueConstraint("document_version_id", name="uq_ingestion_job_version"),
        ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "stage IN ('queued', 'extract', 'chunk', 'embed', 'index', 'done')",
            name="stage_known",
        ),
        CheckConstraint("error_code IS NULL OR error_code ~ '^[a-z_]{1,60}$'", name="error_code"),
    )


class ChunkRecord(TenantScoped, Base):
    """A retrieval-sized piece of a version. Permissions live on the version's ACL and are
    copied into the vector index; a chunk row never carries them (plan data model)."""

    __tablename__ = "chunk"

    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False)
    page: Mapped[int | None] = mapped_column()
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    # Meeting transcripts: where in the meeting this chunk starts and ends (milliseconds).
    start_ms: Mapped[int | None] = mapped_column()
    end_ms: Mapped[int | None] = mapped_column()
    # Keyword-search form of the text, kept in step by PostgreSQL. 'simple' = lower-case
    # words, no stemming and no stop words: it treats Kannada and English alike.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, text)", persisted=True)
    )

    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_chunk_version_ordinal"),
        Index("ix_chunk_search_vector", "search_vector", postgresql_using="gin"),
        ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("token_count > 0", name="token_count_positive"),
        CheckConstraint("octet_length(text_sha256) = 32", name="sha256_length"),
        CheckConstraint("language IN ('en', 'hi', 'kn', 'und')", name="language_known"),
        CheckConstraint(
            "(start_ms IS NULL OR start_ms >= 0) AND "
            "(end_ms IS NULL OR start_ms IS NULL OR end_ms >= start_ms)",
            name="times_ordered",
        ),
    )
