"""The audit log against the real database: completeness, append-only, isolation, fail-closed."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from db.engine import create_session_factory
from db.tenancy import tenant_transaction
from lekha.core.config import AuthMode, Settings
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def client(settings: Settings, org: SeedResult) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings), raise_server_exceptions=False) as test_client:
        yield test_client


async def _events(owner_engine: AsyncEngine, request_id: str) -> list[dict[str, object]]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT organisation_id, actor_user_id, action, status_code, outcome::text,"
                " target_type, target_id, path FROM audit_event WHERE request_id = :r"
            ),
            {"r": request_id},
        )
        return [dict(r._mapping) for r in rows]


def _rid() -> str:
    return f"rid-{uuid.uuid4().hex[:12]}"


async def test_signed_in_request_is_recorded_with_actor_and_target(
    client: TestClient, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    rid, doc = _rid(), org.documents["A1"]
    response = client.get(
        f"/api/v1/documents/{doc}", headers={"X-Dev-User": "dev|asha", "X-Request-ID": rid}
    )
    assert response.status_code == 200
    assert await _events(owner_engine, rid) == [
        {
            "organisation_id": org.organisation_id,
            "actor_user_id": org.users["asha"],
            "action": "GET /api/v1/documents/{document_id}",
            "status_code": 200,
            "outcome": "success",
            "target_type": "document",
            "target_id": doc,
            "path": f"/api/v1/documents/{doc}",
        }
    ]


async def test_denied_access_is_recorded(
    client: TestClient, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    rid = _rid()
    doc = org.documents["A2"]  # asha may not read the legal memo
    headers = {"X-Dev-User": "dev|asha", "X-Request-ID": rid}
    assert client.get(f"/api/v1/documents/{doc}", headers=headers).status_code == 404
    [event] = await _events(owner_engine, rid)
    assert (event["actor_user_id"], event["outcome"], event["target_id"]) == (
        org.users["asha"],
        "denied",
        doc,
    )


async def test_unauthenticated_request_is_recorded_without_identity(
    client: TestClient, owner_engine: AsyncEngine
) -> None:
    rid = _rid()
    assert client.get("/api/v1/me", headers={"X-Request-ID": rid}).status_code == 401
    [event] = await _events(owner_engine, rid)
    assert (event["organisation_id"], event["actor_user_id"], event["outcome"]) == (
        None,
        None,
        "denied",
    )


async def test_health_checks_are_not_recorded(
    client: TestClient, owner_engine: AsyncEngine
) -> None:
    rid = _rid()
    client.get("/health/live", headers={"X-Request-ID": rid})
    assert await _events(owner_engine, rid) == []


async def test_app_role_cannot_change_or_remove_events(app_engine: AsyncEngine) -> None:
    for statement in ("UPDATE audit_event SET status_code = 200", "DELETE FROM audit_event"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_engine.begin() as conn:
                await conn.execute(text(statement))


async def test_even_the_owner_cannot_change_or_remove_events(
    client: TestClient, owner_engine: AsyncEngine
) -> None:
    client.get("/api/v1/me", headers={"X-Request-ID": _rid()})  # ensure a row exists
    for statement in (
        "UPDATE audit_event SET status_code = 200",
        "DELETE FROM audit_event",
        "TRUNCATE audit_event",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with owner_engine.begin() as conn:
                await conn.execute(text(statement))


async def test_tenants_read_only_their_own_events(
    client: TestClient, org: SeedResult, app_engine: AsyncEngine
) -> None:
    client.get("/api/v1/me", headers={"X-Dev-User": "dev|meera", "X-Request-ID": _rid()})
    factory = create_session_factory(app_engine)
    async with tenant_transaction(factory, org.organisation_id) as session:
        result = await session.execute(text("SELECT DISTINCT organisation_id FROM audit_event"))
        orgs = set(result.scalars().all())
    assert orgs == {org.organisation_id}
    async with app_engine.connect() as conn:  # unscoped: sees nothing, not even org-less rows
        assert (await conn.execute(text("SELECT count(*) FROM audit_event"))).scalar_one() == 0


async def test_a_tenant_cannot_write_events_for_another(
    org: SeedResult, app_engine: AsyncEngine
) -> None:
    factory = create_session_factory(app_engine)
    forged = text(
        "INSERT INTO audit_event (request_id, organisation_id, action, method, path,"
        " status_code, outcome) VALUES ('forged', :o, 'GET /x', 'GET', '/x', 200, 'success')"
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_transaction(factory, org.organisation_id) as session:
            await session.execute(forged, {"o": uuid.uuid4()})


async def test_data_is_withheld_when_the_audit_cannot_be_written(
    client: TestClient, owner_engine: AsyncEngine
) -> None:
    """Fail closed for real: with inserts blocked, a readable document list is not returned."""
    async with owner_engine.begin() as conn:
        await conn.execute(text("REVOKE INSERT ON audit_event FROM lekha_app_rw"))
    try:
        response = client.get("/api/v1/documents", headers={"X-Dev-User": "dev|meera"})
    finally:
        async with owner_engine.begin() as conn:
            await conn.execute(text("GRANT INSERT ON audit_event TO lekha_app_rw"))
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "policy" not in response.text  # none of meera's document titles leaked
