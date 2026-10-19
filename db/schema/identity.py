"""Organisations, business units, users, roles, and permission subjects ("badges")."""

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
from db.schema._common import UUID_DEFAULT, TenantScoped, pg_enum
from lekha.auth import Classification, SubjectKind


class Organisation(Base):
    """A tenant. Lekha serves one today (Manipal Group); the schema allows more."""

    __tablename__ = "organisation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=UUID_DEFAULT)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),)


class BusinessUnit(TenantScoped, Base):
    __tablename__ = "business_unit"

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_business_unit_org_id"),
        UniqueConstraint("organisation_id", "code", name="uq_business_unit_org_code"),
        CheckConstraint("code ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="code_format"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
    )


class AppUser(TenantScoped, Base):
    """A person who signs in. ``idp_subject`` is the stable ID from the identity provider."""

    __tablename__ = "app_user"

    idp_subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_app_user_org_id"),
        UniqueConstraint("organisation_id", "idp_subject", name="uq_app_user_org_idp_subject"),
        CheckConstraint("char_length(idp_subject) BETWEEN 1 AND 255", name="idp_subject_length"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 200", name="display_name_length"),
    )


class Role(TenantScoped, Base):
    """A named bundle of powers. ``max_classification`` is the clearance it grants."""

    __tablename__ = "role"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    max_classification: Mapped[Classification] = mapped_column(
        pg_enum(Classification, "classification"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_role_org_id"),
        UniqueConstraint("organisation_id", "name", name="uq_role_org_name"),
        CheckConstraint("name ~ '^[a-z][a-z0-9_-]{0,62}$'", name="name_format"),
    )


class UserRoleGrant(TenantScoped, Base):
    """Records that a user holds a role in one business unit, optionally until ``expires_at``."""

    __tablename__ = "user_role_grant"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    business_unit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        ForeignKeyConstraint(["organisation_id", "role_id"], ["role.organisation_id", "role.id"]),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        UniqueConstraint(
            "user_id", "role_id", "business_unit_id", name="uq_user_role_grant_user_role_bu"
        ),
    )


class PermissionSubject(TenantScoped, Base):
    """A badge that can be placed on a document's guest list.

    ``key`` is the string copied into search-index payloads (``allowed_subjects``). Check
    constraints guarantee it always matches the IDs on the row, so a key can never claim
    to be a different user or role.
    """

    __tablename__ = "permission_subject"

    kind: Mapped[SubjectKind] = mapped_column(pg_enum(SubjectKind, "subject_kind"), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    role_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    group_name: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_permission_subject_org_id"),
        UniqueConstraint("organisation_id", "key", name="uq_permission_subject_org_key"),
        ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["app_user.organisation_id", "app_user.id"]
        ),
        ForeignKeyConstraint(["organisation_id", "role_id"], ["role.organisation_id", "role.id"]),
        ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
        ),
        CheckConstraint(
            "kind <> 'user' OR (user_id IS NOT NULL AND role_id IS NULL"
            " AND business_unit_id IS NULL AND group_name IS NULL"
            " AND key = 'user:' || user_id::text)",
            name="user_shape",
        ),
        CheckConstraint(
            # Role badges always carry their business unit (permissions-v1 §4).
            "kind <> 'role' OR (role_id IS NOT NULL AND business_unit_id IS NOT NULL"
            " AND user_id IS NULL AND group_name IS NULL"
            " AND key = 'role:' || business_unit_id::text || ':' || role_id::text)",
            name="role_shape",
        ),
        CheckConstraint(
            "kind <> 'group' OR (group_name ~ '^[A-Za-z0-9._-]{1,128}$'"
            " AND user_id IS NULL AND role_id IS NULL AND business_unit_id IS NULL"
            " AND key = 'group:' || group_name)",
            name="group_shape",
        ),
        Index("ix_permission_subject_user_id", "user_id"),
    )
