"""Column helpers shared by all tenant-scoped tables."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

UUID_DEFAULT = text("gen_random_uuid()")


def pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """A PostgreSQL enum that stores the members' *values*, in declaration order."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class TenantScoped:
    """Primary key, owning organisation, and creation time.

    Every tenant table repeats ``organisation_id`` so that row-level security can filter
    it directly and composite foreign keys can stop cross-tenant references.
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=UUID_DEFAULT)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organisation.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
