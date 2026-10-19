"""Idempotency-Key against the real database, object store and upload endpoint."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from db.engine import create_session_factory
from db.idempotency import ClaimOutcome, IdempotencyStore, KeyScope
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n% idempotency\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
TTL = timedelta(hours=24)
STALE = timedelta(minutes=5)


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    # A second curator in alpha, to show keys are scoped per user.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO user_role_grant (organisation_id, user_id, role_id, business_unit_id)"
                " SELECT :o, :u, r.id, :b FROM role r WHERE r.organisation_id = :o"
                " AND r.name = 'curator'"
            ),
            {
                "o": result.organisation_id,
                "u": result.users["meera"],
                "b": result.business_units["bu-alpha"],
            },
        )
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def client(settings: Settings, org: SeedResult) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings), raise_server_exceptions=False) as test_client:
        yield test_client


def _upload(
    client: TestClient,
    org: SeedResult,
    key: str | None,
    *,
    user: str = "chitra",
    title: str = "Idempotent handbook",
    canonical_key: str = "synthetic:idem",
    acl: list[str] | None = None,
) -> Any:  # Any: the httpx response from TestClient
    headers = {"X-Dev-User": f"dev|{user}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    metadata = {
        "title": title,
        "canonical_key": canonical_key,
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "classification": "internal",
        "acl_subjects": acl or [user_badge(org.users["chitra"])],
    }
    return client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("handbook.pdf", PDF, "application/pdf")},
        data={"metadata": json.dumps(metadata)},
    )


async def _documents(owner_engine: AsyncEngine, org: SeedResult, canonical_key: str) -> int:
    async with owner_engine.connect() as conn:
        count: int = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM document WHERE organisation_id = :o"
                    " AND canonical_key = :k"
                ),
                {"o": org.organisation_id, "k": canonical_key},
            )
        ).scalar_one()
    return count


def _key() -> str:
    return str(uuid.uuid4())


async def test_a_retried_upload_replays_instead_of_duplicating(
    client: TestClient, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    key = _key()
    first = _upload(client, org, key)
    retry = _upload(client, org, key)
    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()
    assert retry.headers["Location"] == first.headers["Location"]
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert await _documents(owner_engine, org, "synthetic:idem") == 1


def test_without_a_key_a_retry_is_a_conflict(client: TestClient, org: SeedResult) -> None:
    assert _upload(client, org, None).status_code == 201
    assert _upload(client, org, None).status_code == 409  # canonical_key already used


def test_reusing_a_key_for_a_different_request_is_refused(
    client: TestClient, org: SeedResult
) -> None:
    key = _key()
    assert _upload(client, org, key).status_code == 201
    other = _upload(client, org, key, title="A different title")
    assert other.status_code == 409
    assert "different request" in other.json()["error"]["message"]


async def test_keys_are_private_to_each_user(
    client: TestClient, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    key = _key()
    assert _upload(client, org, key, canonical_key="synthetic:a").status_code == 201
    # meera uses the same key string: it is a different key, so her upload really happens.
    theirs = _upload(client, org, key, user="meera", canonical_key="synthetic:b")
    assert theirs.status_code == 201
    assert "Idempotent-Replayed" not in theirs.headers
    assert await _documents(owner_engine, org, "synthetic:b") == 1


def test_a_failed_attempt_frees_the_key(client: TestClient, org: SeedResult) -> None:
    key = _key()
    failed = _upload(client, org, key, acl=["group:does-not-exist"])
    assert failed.status_code == 422
    # The corrected request may reuse the key: the failed attempt created nothing.
    assert _upload(client, org, key).status_code == 201


def test_malformed_key_is_rejected_before_anything_happens(
    client: TestClient, org: SeedResult
) -> None:
    response = _upload(client, org, "bad key")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


# --- claim state machine, directly against the store -------------------------------


@pytest.fixture
def store(app_engine: AsyncEngine) -> IdempotencyStore:
    return IdempotencyStore(create_session_factory(app_engine), ttl=TTL, stale_after=STALE)


def _scope(org: SeedResult) -> KeyScope:
    return KeyScope(org.organisation_id, org.users["chitra"], _key())


async def test_claims_follow_the_state_machine(store: IdempotencyStore, org: SeedResult) -> None:
    scope, now, fp = _scope(org), datetime.now(UTC), b"f" * 32
    claim = store.claim
    assert (await claim(scope, operation="op", fingerprint=fp, now=now)).outcome is ClaimOutcome.NEW
    second = await claim(scope, operation="op", fingerprint=fp, now=now)
    assert second.outcome is ClaimOutcome.IN_PROGRESS
    # Abandoned (no progress for longer than STALE): the same request may take it over.
    later = now + STALE + timedelta(seconds=1)
    taken = await claim(scope, operation="op", fingerprint=fp, now=later)
    assert taken.outcome is ClaimOutcome.NEW
    await store.complete(scope, status=201, body={"ok": True}, location=None)
    replay = await claim(scope, operation="op", fingerprint=fp, now=later)
    assert (replay.outcome, replay.status, replay.body) == (ClaimOutcome.REPLAY, 201, {"ok": True})
    # After expiry the key is free again, even for a different request. (The takeover
    # restarted the 24-hour clock, so expiry is measured from `later`.)
    not_yet = later + TTL - timedelta(seconds=1)
    early = await claim(scope, operation="op", fingerprint=b"g" * 32, now=not_yet)
    assert early.outcome is ClaimOutcome.MISMATCH
    expired = later + TTL + timedelta(seconds=1)
    fresh = await claim(scope, operation="op", fingerprint=b"g" * 32, now=expired)
    assert fresh.outcome is ClaimOutcome.NEW


async def test_concurrent_claims_on_one_key_admit_exactly_one(
    store: IdempotencyStore, org: SeedResult
) -> None:
    scope, now = _scope(org), datetime.now(UTC)
    outcomes = await asyncio.gather(
        *(store.claim(scope, operation="op", fingerprint=b"f" * 32, now=now) for _ in range(5))
    )
    kinds = sorted(o.outcome.value for o in outcomes)
    assert kinds == ["in_progress"] * 4 + ["new"]
