"""The shared glossary (Phase 4): entries and every name each one goes by."""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped


class GlossaryEntry(TenantScoped, Base):
    """A thing with several names. Owned by a business unit (its curators manage it);
    ``shared`` entries help everyone in the organisation, others only that unit."""

    __tablename__ = "glossary_entry"

    business_unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    shared: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_glossary_entry_org_id"),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "created_by"], ["app_user.organisation_id", "app_user.id"]
        ),
        CheckConstraint("char_length(canonical) BETWEEN 1 AND 120", name="canonical_length"),
        CheckConstraint("char_length(description) <= 500", name="description_length"),
    )


class GlossaryName(TenantScoped, Base):
    """One name of an entry (its canonical name included). A name means one thing per
    organisation, so ``name_norm`` is unique."""

    __tablename__ = "glossary_name"

    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "name_norm", name="uq_glossary_name_norm"),
        ForeignKeyConstraint(
            ["organisation_id", "entry_id"],
            ["glossary_entry.organisation_id", "glossary_entry.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_length"),
        Index("ix_glossary_name_entry", "entry_id"),
    )
