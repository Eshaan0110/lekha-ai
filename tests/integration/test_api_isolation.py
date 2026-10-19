"""End-to-end permission isolation through the real API, database and row-level security.

Uses the synthetic matrix from ``apps/scripts/seed_dev_data.py``, seeded into the
throwaway test database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from db.access import list_readable_documents, load_auth_context
from db.engine import create_session_factory
from db.tenancy import tenant_transaction
from lekha.auth import (
    AccessLevel,
    AclEntry,
    Classification,
    DocumentAccessFacts,
    VerifiedIdentity,
    can_read,
)
from lekha.core.config import AuthMode, Settings
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

# Who must see what (see the table in seed_dev_data.py).
EXPECTED: dict[str, set[str]] = {
    "asha": {"A1"},
    "meera": {"A1", "A2", "A5"},
    "ravi": {"B1"},
    "chitra": set(),
    "kiran": set(),
    "nobody": set(),
}


async def _seed(owner_engine: AsyncEngine) -> SeedResult:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        return await seed_synthetic_organisation(session, uuid.uuid4())


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    result = await _seed(owner_engine)
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
async def other_org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    """A second organisation with the same user names and document titles."""
    result = await _seed(owner_engine)
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def client(settings: Settings, org: SeedResult) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings)) as test_client:
        yield test_client


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _labels(org: SeedResult, ids: set[str]) -> set[str]:
    by_id = {str(v): k for k, v in org.documents.items()}
    return {by_id.get(i, f"FOREIGN:{i}") for i in ids}


@pytest.mark.parametrize(("user", "expected"), sorted(EXPECTED.items()))
def test_each_user_lists_exactly_their_documents(
    client: TestClient, org: SeedResult, user: str, expected: set[str]
) -> None:
    response = client.get("/api/v1/documents", headers=_as(user))
    assert response.status_code == 200
    assert _labels(org, {d["id"] for d in response.json()["items"]}) == expected


@pytest.mark.parametrize("user", sorted(EXPECTED))
def test_opening_a_document_directly_obeys_the_same_rule(
    client: TestClient, org: SeedResult, user: str
) -> None:
    for label, doc_id in org.documents.items():
        response = client.get(f"/api/v1/documents/{doc_id}", headers=_as(user))
        expected = 200 if label in EXPECTED[user] else 404
        assert response.status_code == expected, f"{user} opening {label}"
        if expected == 404:
            assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("user", ["inactive", "ghost"])
def test_inactive_and_unknown_users_cannot_sign_in(client: TestClient, user: str) -> None:
    assert client.get("/api/v1/me", headers=_as(user)).status_code == 401


def test_another_organisations_data_is_invisible(
    client: TestClient, org: SeedResult, other_org: SeedResult
) -> None:
    # Same names exist in both organisations; signing in resolves within ours only.
    me = client.get("/api/v1/me", headers=_as("meera")).json()
    assert me["user_id"] == str(org.users["meera"])
    listed = {
        d["id"] for d in client.get("/api/v1/documents", headers=_as("meera")).json()["items"]
    }
    assert listed.isdisjoint({str(i) for i in other_org.documents.values()})
    for doc_id in other_org.documents.values():
        response = client.get(f"/api/v1/documents/{doc_id}", headers=_as("meera"))
        assert response.status_code == 404


def test_paging_returns_every_readable_document_exactly_once(
    client: TestClient, org: SeedResult
) -> None:
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": "1", **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/v1/documents", headers=_as("meera"), params=params).json()
        seen += [d["id"] for d in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    assert _labels(org, set(seen)) == EXPECTED["meera"]


def test_tampered_cursor_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/documents?cursor=not*base64", headers=_as("meera"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


async def _current_version_facts(
    owner_engine: AsyncEngine, organisation_id: uuid.UUID
) -> dict[uuid.UUID, DocumentAccessFacts]:
    async with owner_engine.connect() as conn:
        docs = (
            await conn.execute(
                text(
                    "SELECT d.id, d.business_unit_id, d.classification::text,"
                    " d.deleted_at IS NOT NULL, v.id, v.deleted_at IS NOT NULL"
                    " FROM document d JOIN document_version v ON v.id = d.current_version_id"
                    " WHERE d.organisation_id = :o"
                ),
                {"o": organisation_id},
            )
        ).all()
        acl_rows = (
            await conn.execute(
                text(
                    "SELECT a.document_version_id, s.key, a.access_level::text, a.expires_at"
                    " FROM document_acl a JOIN permission_subject s ON s.id = a.subject_id"
                    " WHERE a.organisation_id = :o"
                ),
                {"o": organisation_id},
            )
        ).all()
    acl: dict[uuid.UUID, list[AclEntry]] = {}
    for version_id, key, level, expires_at in acl_rows:
        acl.setdefault(version_id, []).append(AclEntry(key, AccessLevel(level), expires_at))
    return {
        doc_id: DocumentAccessFacts(
            organisation_id=organisation_id,
            business_unit_id=bu,
            classification=Classification(classification),
            acl=tuple(acl.get(version_id, [])),
            document_deleted=doc_deleted,
            version_deleted=version_deleted,
            is_current_version=True,
        )
        for doc_id, bu, classification, doc_deleted, version_id, version_deleted in docs
    }


async def test_sql_filter_agrees_with_the_reference_rule(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, org: SeedResult
) -> None:
    """The database query and lekha.auth.can_read must give identical answers."""
    now = datetime.now(UTC)
    facts = await _current_version_facts(owner_engine, org.organisation_id)
    factory = create_session_factory(app_engine)
    for user in (*EXPECTED, "inactive"):
        async with tenant_transaction(factory, org.organisation_id) as session:
            identity = VerifiedIdentity(org.organisation_id, f"dev|{user}")
            ctx = await load_auth_context(session, identity, now=now)
            if ctx is None:
                assert user == "inactive"
                continue
            via_sql = {
                d.id for d in await list_readable_documents(session, ctx, now=now, limit=100)
            }
        via_rule = {doc_id for doc_id, f in facts.items() if can_read(ctx, f, now=now)}
        assert via_sql == via_rule, user
