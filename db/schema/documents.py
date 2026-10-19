"""Documents, their immutable versions, stored files, and per-version guest lists (ACLs)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum
from lekha.auth import AccessLevel, AclSource, Classification

SHA256_BYTES = 32


class SourceSystem(TenantScoped, Base):
    """Where documents come from (``upload`` in Phase 1; connectors later)."""

    __tablename__ = "source_system"

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_source_system_org_id"),
        UniqueConstraint("organisation_id", "code", name="uq_source_system_org_code"),
        CheckConstraint("code ~ '^[a-z][a-z0-9_-]{0,62}$'", name="code_format"),
    )


class Document(TenantScoped, Base):
    """A real-world thing (a policy, a letter). Its content lives in versions."""

    __tablename__ = "document"

    business_unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_system_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_system_external_id: Mapped[str | None] = mapped_column(Text)
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[Classification] = mapped_column(
        pg_enum(Classification, "classification"), nullable=False
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    retention_until: Mapped[date | None] = mapped_column(Date)
    legal_hold: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_document_org_id"),
        UniqueConstraint("organisation_id", "canonical_key", name="uq_document_org_canonical_key"),
        # Catches duplicate imports of the same item from the same source.
        UniqueConstraint(
            "organisation_id",
            "source_system_id",
            "source_system_external_id",
            name="uq_document_org_source_external_id",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "source_system_id"],
            ["source_system.organisation_id", "source_system.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "current_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            use_alter=True,  # document <-> document_version reference each other
            name="fk_document_current_version",
        ),
        CheckConstraint("char_length(title) BETWEEN 1 AND 500", name="title_length"),
        CheckConstraint(
            "char_length(canonical_key) BETWEEN 1 AND 255", name="canonical_key_length"
        ),
        Index("ix_document_business_unit_id", "business_unit_id"),
    )


class DocumentFile(TenantScoped, Base):
    """The stored bytes of one uploaded file, identified by checksum."""

    __tablename__ = "document_file"

    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_document_file_org_id"),
        CheckConstraint(f"octet_length(sha256) = {SHA256_BYTES}", name="sha256_length"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "char_length(original_filename) BETWEEN 1 AND 255", name="original_filename_length"
        ),
    )


class DocumentVersion(TenantScoped, Base):
    """One immutable snapshot of a document. A newer version supersedes it."""

    __tablename__ = "document_version"

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    version_label: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_document_version_org_id"),
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_version_document_number"
        ),
        ForeignKeyConstraint(
            ["organisation_id", "document_id"], ["document.organisation_id", "document.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "file_id"], ["document_file.organisation_id", "document_file.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "superseded_by_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name="fk_document_version_superseded_by",
        ),
        CheckConstraint("version_number > 0", name="version_number_positive"),
    )


class DocumentAcl(TenantScoped, Base):
    """One guest-list entry: this badge may access this version at this level."""

    __tablename__ = "document_acl"

    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    access_level: Mapped[AccessLevel] = mapped_column(
        pg_enum(AccessLevel, "access_level"), nullable=False
    )
    source: Mapped[AclSource] = mapped_column(pg_enum(AclSource, "acl_source"), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "subject_id"],
            ["permission_subject.organisation_id", "permission_subject.id"],
        ),
        UniqueConstraint(
            "document_version_id",
            "subject_id",
            "access_level",
            name="uq_document_acl_version_subject_level",
        ),
        Index("ix_document_acl_subject_id", "subject_id"),
    )
