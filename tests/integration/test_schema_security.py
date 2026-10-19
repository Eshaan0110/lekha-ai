"""Database-level security guarantees for the identity and document schema.

These prove the controls that do not depend on application code:
least-privilege app role, tenant row-level security, cross-tenant foreign keys, and
permission-subject ("badge") integrity.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from asyncpg.exceptions import InvalidPasswordError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

import db.schema  # noqa: F401  - registers mappings on Base.metadata
from db.base import Base
from db.engine import create_session_factory
from db.tenancy import tenant_transaction
from lekha.auth import Classification
from tests.integration.support import TENANT_TABLES_CHILD_FIRST, delete_tenant

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Tenant:
    organisation_id: uuid.UUID
    business_unit_id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    version_id: uuid.UUID
    document_id: uuid.UUID


async def _insert(conn: AsyncConnection, sql: str, **params: object) -> uuid.UUID:
    result = await conn.execute(text(sql + " RETURNING id"), params)
    value = result.scalar_one()
    assert isinstance(value, uuid.UUID)
    return value


async def _seed_tenant(engine: AsyncEngine, label: str) -> Tenant:
    """Create one organisation with a synthetic document visible to one user."""
    suffix = uuid.uuid4().hex[:8]
    async with engine.begin() as conn:
        org = await _insert(
            conn, "INSERT INTO organisation (name) VALUES (:n)", n=f"{label}-{suffix}"
        )
        bu = await _insert(
            conn,
            "INSERT INTO business_unit (organisation_id, code, name) VALUES (:o, :c, :n)",
            o=org,
            c=f"bu-{suffix}",
            n=f"{label} unit",
        )
        user = await _insert(
            conn,
            "INSERT INTO app_user (organisation_id, idp_subject, display_name) VALUES (:o, :s, :n)",
            o=org,
            s=f"dev|{label}-{suffix}",
            n=f"{label} tester",
        )
        role = await _insert(
            conn,
            "INSERT INTO role (organisation_id, name, max_classification)"
            " VALUES (:o, 'viewer', 'internal')",
            o=org,
        )
        subject = await _insert(
            conn,
            "INSERT INTO permission_subject (organisation_id, kind, key, user_id)"
            " VALUES (:o, 'user', :k, :u)",
            o=org,
            k=f"user:{user}",
            u=user,
        )
        source = await _insert(
            conn,
            "INSERT INTO source_system (organisation_id, code, name)"
            " VALUES (:o, 'upload', 'Upload')",
            o=org,
        )
        file_ = await _insert(
            conn,
            "INSERT INTO document_file (organisation_id, storage_key, sha256, size_bytes,"
            " media_type, original_filename) VALUES (:o, :k, :h, 10, 'application/pdf', 'x.pdf')",
            o=org,
            k=f"{org}/{suffix}.pdf",
            h=hashlib.sha256(suffix.encode()).digest(),
        )
        doc = await _insert(
            conn,
            "INSERT INTO document (organisation_id, business_unit_id, source_system_id,"
            " canonical_key, title, classification)"
            " VALUES (:o, :b, :s, :k, :t, 'internal')",
            o=org,
            b=bu,
            s=source,
            k=f"policy:{suffix}",
            t=f"{label} synthetic policy",
        )
        version = await _insert(
            conn,
            "INSERT INTO document_version (organisation_id, document_id, version_number, file_id)"
            " VALUES (:o, :d, 1, :f)",
            o=org,
            d=doc,
            f=file_,
        )
        await conn.execute(
            text("UPDATE document SET current_version_id = :v WHERE id = :d"),
            {"v": version, "d": doc},
        )
        await _insert(
            conn,
            "INSERT INTO document_acl (organisation_id, document_version_id, subject_id,"
            " access_level, source) VALUES (:o, :v, :s, 'read', 'explicit')",
            o=org,
            v=version,
            s=subject,
        )
    return Tenant(org, bu, user, role, version, doc)


@pytest.fixture
async def tenants(owner_engine: AsyncEngine) -> AsyncIterator[tuple[Tenant, Tenant]]:
    a = await _seed_tenant(owner_engine, "alpha")
    b = await _seed_tenant(owner_engine, "beta")
    yield a, b
    for t in (a, b):
        await delete_tenant(owner_engine, t.organisation_id)


# --- least privilege -------------------------------------------------------------------


async def test_app_role_cannot_change_the_schema(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("CREATE TABLE sneaky (id int)"))


async def test_app_role_cannot_drop_tables(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="must be owner"):
        async with app_engine.begin() as conn:
            await conn.execute(text("DROP TABLE document_acl"))


async def test_app_role_cannot_create_organisations(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("INSERT INTO organisation (name) VALUES ('rogue')"))


async def test_app_role_is_not_privileged(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == (False, False)


# --- row-level security ----------------------------------------------------------------


@pytest.mark.usefixtures("tenants")
async def test_unscoped_app_transaction_sees_no_tenant_rows(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        for table in (*TENANT_TABLES_CHILD_FIRST, "organisation"):
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, f"{table} leaked {count} rows without a tenant scope"


async def test_scoped_transaction_sees_only_its_own_tenant(
    app_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    factory = create_session_factory(app_engine)
    async with tenant_transaction(factory, a.organisation_id) as session:
        docs = (await session.execute(text("SELECT id FROM document"))).scalars().all()
        orgs = (await session.execute(text("SELECT id FROM organisation"))).scalars().all()
    assert docs == [a.document_id]
    assert orgs == [a.organisation_id]
    assert b.document_id not in docs


async def test_tenant_scope_does_not_outlive_the_transaction(
    app_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    factory = create_session_factory(app_engine)
    async with tenant_transaction(factory, a.organisation_id) as session:
        pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        assert (await session.execute(text("SELECT count(*) FROM document"))).scalar_one() == 1
    # Same pooled server connection, next transaction: the scope must be gone.
    async with app_engine.connect() as conn:
        assert (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one() == pid
        count = (await conn.execute(text("SELECT count(*) FROM document"))).scalar_one()
    assert count == 0


async def test_cannot_write_rows_into_another_tenant(
    app_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    factory = create_session_factory(app_engine)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_transaction(factory, a.organisation_id) as session:
            await session.execute(
                text(
                    "INSERT INTO business_unit (organisation_id, code, name)"
                    " VALUES (:o, 'smuggled', 'Smuggled')"
                ),
                {"o": b.organisation_id},
            )


async def test_cannot_update_another_tenants_rows(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    factory = create_session_factory(app_engine)
    async with tenant_transaction(factory, a.organisation_id) as session:
        updated = await session.execute(
            text("UPDATE document SET title = 'hijacked' WHERE id = :d RETURNING id"),
            {"d": b.document_id},
        )
        assert updated.all() == []
    async with owner_engine.connect() as conn:
        title = (
            await conn.execute(
                text("SELECT title FROM document WHERE id = :d"), {"d": b.document_id}
            )
        ).scalar_one()
    assert title != "hijacked"


# --- cross-tenant references and badge integrity --------------------------------------


async def test_acl_cannot_point_at_another_tenants_document(
    owner_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = tenants
    # Even the owner account (no RLS) cannot link a's badge to b's document version.
    async with owner_engine.connect() as conn:
        subject = (
            await conn.execute(
                text("SELECT id FROM permission_subject WHERE organisation_id = :o"),
                {"o": a.organisation_id},
            )
        ).scalar_one()
    with pytest.raises(IntegrityError, match="foreign key"):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO document_acl (organisation_id, document_version_id, subject_id,"
                    " access_level, source) VALUES (:o, :v, :s, 'read', 'explicit')"
                ),
                {"o": a.organisation_id, "v": b.version_id, "s": subject},
            )


async def test_role_badge_must_name_its_business_unit(
    owner_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    insert = text(
        "INSERT INTO permission_subject (organisation_id, kind, key, role_id, business_unit_id)"
        " VALUES (:o, 'role', :k, :r, :b)"
    )
    params = {"o": a.organisation_id, "r": a.role_id, "b": a.business_unit_id}
    # The un-scoped form from the plan's examples ("role:<role>") is refused.
    with pytest.raises(IntegrityError, match="ck_permission_subject_role_shape"):
        async with owner_engine.begin() as conn:
            await conn.execute(insert, {**params, "k": f"role:{a.role_id}"})
    async with owner_engine.begin() as conn:
        await conn.execute(insert, {**params, "k": f"role:{a.business_unit_id}:{a.role_id}"})


async def test_user_badge_cannot_claim_a_different_user(
    owner_engine: AsyncEngine, tenants: tuple[Tenant, Tenant]
) -> None:
    a, _ = tenants
    with pytest.raises(IntegrityError, match="ck_permission_subject_user_shape"):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO permission_subject (organisation_id, kind, key, user_id)"
                    " VALUES (:o, 'user', :k, :u)"
                ),
                {"o": a.organisation_id, "k": f"user:{uuid.uuid4()}", "u": a.user_id},
            )


async def test_database_classification_order_matches_python(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        values = (
            (await conn.execute(text("SELECT unnest(enum_range(NULL::classification))::text")))
            .scalars()
            .all()
        )
        lower = (
            await conn.execute(text("SELECT 'internal'::classification < 'confidential'"))
        ).scalar_one()
    assert values == [c.value for c in Classification]
    assert lower is True


async def test_app_login_is_rejected_with_wrong_password(app_engine: AsyncEngine) -> None:
    url = app_engine.url.set(password="definitely-wrong-password")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        with pytest.raises(InvalidPasswordError):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()


async def test_every_tenant_table_has_row_level_security(owner_engine: AsyncEngine) -> None:
    """Guards future migrations: a new table with organisation_id must also get RLS."""
    tenant_tables = {
        table.name for table in Base.metadata.sorted_tables if "organisation_id" in table.c
    } | {"organisation"}
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity,"
                    " EXISTS (SELECT 1 FROM pg_policies p"
                    "         WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation')"
                    " FROM pg_class c WHERE c.relname = ANY(:tables)"
                ),
                {"tables": sorted(tenant_tables)},
            )
        ).all()
    found = {name: (rls, policy) for name, rls, policy in rows}
    assert set(found) == tenant_tables
    missing = sorted(name for name, (rls, policy) in found.items() if not (rls and policy))
    assert missing == [], f"tables without row-level security: {missing}"
