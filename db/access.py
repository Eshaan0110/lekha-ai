"""Authorisation queries: build an AuthContext, and fetch only documents the caller may read.

``readable_condition`` is the SQL form of ``lekha.auth.can_read``. Filtering happens inside the
query, so rows the caller may not see never leave the database. Every function expects a
session opened with ``db.tenancy.tenant_transaction`` (row-level security adds the tenant
boundary underneath).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, cast, exists, false, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import (
    AppUser,
    Document,
    DocumentAcl,
    DocumentVersion,
    PermissionSubject,
    Role,
    UserRoleGrant,
)
from lekha.auth import (
    AccessLevel,
    AuthContext,
    Classification,
    RoleGrant,
    VerifiedIdentity,
    build_auth_context,
)


@dataclass(frozen=True, slots=True)
class ReadableDocument:
    id: uuid.UUID
    business_unit_id: uuid.UUID
    title: str
    classification: Classification
    version_id: uuid.UUID
    version_number: int


async def load_auth_context(
    session: AsyncSession, identity: VerifiedIdentity, *, now: datetime
) -> AuthContext | None:
    """Return the caller's AuthContext, or None if they are unknown or deactivated."""
    user = (
        await session.execute(
            select(AppUser).where(
                AppUser.organisation_id == identity.organisation_id,
                AppUser.idp_subject == identity.idp_subject,
                AppUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if user is None:
        return None
    rows = await session.execute(
        select(
            UserRoleGrant.business_unit_id,
            UserRoleGrant.role_id,
            UserRoleGrant.expires_at,
            Role.name,
            Role.max_classification,
        )
        .join(Role, Role.id == UserRoleGrant.role_id)
        .where(UserRoleGrant.user_id == user.id)
    )
    grants = [
        RoleGrant(
            business_unit_id=r.business_unit_id,
            role_id=r.role_id,
            role_name=r.name,
            max_classification=r.max_classification,
            expires_at=r.expires_at,
        )
        for r in rows
    ]
    return build_auth_context(
        user_id=user.id,
        organisation_id=user.organisation_id,
        display_name=user.display_name,
        grants=grants,
        groups=identity.groups,
        now=now,
    )


def _classification(value: Classification) -> ColumnElement[Classification]:
    # Compare as the PostgreSQL enum, whose declaration order is public < ... < restricted.
    return cast(literal(value.value), Document.__table__.c.classification.type)


def readable_condition(ctx: AuthContext, *, now: datetime) -> ColumnElement[bool]:
    """SQL form of ``can_read`` for a query joining Document to its current DocumentVersion."""
    within_clearance = [
        and_(Document.business_unit_id == bu, Document.classification <= _classification(c))
        for bu, c in ctx.clearance_by_bu.items()
    ]
    if not within_clearance or not ctx.badges:
        return false()  # no roles or no badges: nothing is readable
    on_guest_list = exists().where(
        DocumentAcl.document_version_id == DocumentVersion.id,
        DocumentAcl.access_level == AccessLevel.READ,
        or_(DocumentAcl.expires_at.is_(None), DocumentAcl.expires_at > now),
        PermissionSubject.id == DocumentAcl.subject_id,
        PermissionSubject.key.in_(sorted(ctx.badges)),
    )
    return and_(
        Document.organisation_id == ctx.organisation_id,
        or_(*within_clearance),
        Document.deleted_at.is_(None),
        DocumentVersion.deleted_at.is_(None),
        on_guest_list,
    )


_ReadableRow = tuple[uuid.UUID, uuid.UUID, str, Classification, uuid.UUID, int]


def _readable_documents(ctx: AuthContext, now: datetime) -> Select[_ReadableRow]:
    return (
        select(
            Document.id,
            Document.business_unit_id,
            Document.title,
            Document.classification,
            DocumentVersion.id.label("version_id"),
            DocumentVersion.version_number,
        )
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(readable_condition(ctx, now=now))
    )


async def list_readable_documents(
    session: AsyncSession,
    ctx: AuthContext,
    *,
    now: datetime,
    limit: int,
    after: uuid.UUID | None = None,
) -> list[ReadableDocument]:
    """Documents the caller may read, ordered by id, starting after ``after`` (keyset paging)."""
    query = _readable_documents(ctx, now).order_by(Document.id).limit(limit)
    if after is not None:
        query = query.where(Document.id > after)
    return [ReadableDocument(**row._mapping) for row in await session.execute(query)]


async def get_readable_document(
    session: AsyncSession, ctx: AuthContext, document_id: uuid.UUID, *, now: datetime
) -> ReadableDocument | None:
    """The document if the caller may read it; None if it is missing *or* not visible."""
    row = (
        await session.execute(_readable_documents(ctx, now).where(Document.id == document_id))
    ).first()
    return ReadableDocument(**row._mapping) if row is not None else None
