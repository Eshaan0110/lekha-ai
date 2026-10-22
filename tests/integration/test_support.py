"""Pilot support end to end: logging unanswered questions, curator follow-up, audit reader."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.api.ratelimit import RateDecision
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from lekha.core.config import AuthMode, Settings
from tests.fixtures.fake_llm import FakeLanguageModel
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration


class NullQueue:
    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return None

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_action(self, organisation_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
        return None

    async def close(self) -> None:
        return None


class AllowAll:
    async def hit(self, scope: str, user_id: uuid.UUID) -> RateDecision:
        return RateDecision(allowed=True, limit=100, remaining=99, retry_after=60)


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
    app = create_app(
        app_settings,
        job_queue=NullQueue(),
        language_model=FakeLanguageModel(),
        ask_limiter=AllowAll(),
    )
    with TestClient(app) as test_client:
        yield test_client


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _ask(client: TestClient, user: str, question: str) -> Any:
    return client.post("/api/v1/ask", headers=_as(user), json={"question": question}).json()


def _log(
    client: TestClient, org: SeedResult, user: str, query_id: str, unit: str = "bu-alpha"
) -> Any:
    return client.post(
        f"/api/v1/answers/{query_id}/request",
        headers=_as(user),
        json={"business_unit_id": str(org.business_units[unit]), "note": "Please add this policy"},
    )


def test_an_unanswered_question_can_be_logged_and_followed_up_by_curators(
    client: TestClient, org: SeedResult
) -> None:
    answer = _ask(client, "asha", "Is there a gym in the office?")
    assert answer["abstained"] is True
    query_id = answer["query_id"]

    assert _log(client, org, "meera", query_id).status_code == 404  # not her question
    assert _log(client, org, "asha", query_id, unit="bu-beta").status_code == 403  # not her unit
    logged = _log(client, org, "asha", query_id)
    assert logged.status_code == 201
    assert _log(client, org, "asha", query_id).status_code == 409  # only once

    def requests(user: str) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = client.get("/api/v1/requests", headers=_as(user)).json()
        return listed

    [seen] = requests("chitra")  # Alpha's curator
    assert (seen["question"], seen["asked_by"], seen["status"]) == (
        "Is there a gym in the office?",
        "Asha (synthetic)",
        "open",
    )
    [own] = requests("asha")
    assert own["mine"] is True
    for user in ("bina", "ravi", "meera", "nobody"):
        assert requests(user) == [], user

    url = f"/api/v1/requests/{seen['id']}/resolve"
    body = {"status": "resolved", "resolution": "Uploaded the facilities policy."}
    assert client.post(url, headers=_as("bina"), json=body).status_code == 404  # Beta's curator
    resolved = client.post(url, headers=_as("chitra"), json=body)
    assert resolved.status_code == 200
    assert resolved.json()["resolution"] == "Uploaded the facilities policy."
    assert client.post(url, headers=_as("chitra"), json=body).status_code == 409
    assert requests("asha")[0]["status"] == "resolved"  # the asker sees the outcome
    open_only = client.get("/api/v1/requests?status=open", headers=_as("chitra")).json()
    assert open_only == []


def test_the_audit_log_is_for_audit_readers_and_scoped_to_their_unit(
    client: TestClient, org: SeedResult
) -> None:
    _ask(client, "asha", "Is there a gym in the office?")
    client.get("/api/v1/me", headers=_as("ravi"))  # Beta activity: outside Tara's scope

    assert client.get("/api/v1/audit", headers=_as("asha")).status_code == 403
    assert client.get("/api/v1/audit", headers=_as("chitra")).status_code == 403

    items = client.get("/api/v1/audit?limit=100", headers=_as("tara")).json()["items"]
    actors = {i["actor"] for i in items}
    assert "Asha (synthetic)" in actors
    assert "Ravi (synthetic)" not in actors
    asked = next(i for i in items if i["path"] == "/api/v1/ask")
    assert (asked["method"], asked["status_code"], asked["target_type"]) == (
        "POST",
        200,
        "query_log",
    )


def test_the_audit_log_pages_without_gaps_or_repeats(client: TestClient, org: SeedResult) -> None:
    for _ in range(5):
        client.get("/api/v1/me", headers=_as("asha"))
    first = client.get("/api/v1/audit?limit=2", headers=_as("tara")).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = client.get(
        f"/api/v1/audit?limit=2&cursor={first['next_cursor']}", headers=_as("tara")
    ).json()
    ids = [i["request_id"] for i in first["items"] + second["items"]]
    assert len(set(ids)) == 4
    assert client.get("/api/v1/audit?cursor=not-a-cursor", headers=_as("tara")).status_code == 422
