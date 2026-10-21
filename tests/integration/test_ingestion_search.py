"""End to end on the real stack: upload → worker pipeline → permission-filtered search,
new versions, and the deletion workflow."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.wiring import build_vector_index
from apps.worker.pipeline import PipelineDeps, TransientJobError, run_ingestion
from apps.worker.purge import run_purge
from db.engine import create_session_factory
from db.schema import DeletionStatus, IngestionStatus
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory import ObjectStoreError
from lekha.memory.s3_store import S3ObjectStore
from lekha.retrieval import QdrantIndex, UnscopedSearchError, VectorIndexError
from model_layer import FakeEmbedder
from tests.fixtures.synthetic_docs import make_docx, make_image_only_pdf
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

POLICY = make_docx(
    [
        ("Annual leave", ["Employees receive twenty days of annual leave every calendar year."]),
        ("वार्षिक अवकाश", ["कर्मचारियों को हर साल बीस दिन का वार्षिक अवकाश मिलता है।"]),
    ]
)


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.purges: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.reindexes: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        self.purges.append((organisation_id, request_id))

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        self.reindexes.append((organisation_id, job_id))

    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        self.jobs.append((organisation_id, job_id))

    async def close(self) -> None:
        return None


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def client(settings: Settings, org: SeedResult, queue: RecordingQueue) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings, job_queue=queue)) as test_client:
        yield test_client


@pytest.fixture
async def deps(settings: Settings, app_engine: AsyncEngine) -> AsyncIterator[PipelineDeps]:
    index = build_vector_index(settings, FakeEmbedder.dimensions)
    yield PipelineDeps(
        session_factory=create_session_factory(app_engine),
        store=S3ObjectStore.from_settings(settings),
        index=index,
        embedder=FakeEmbedder(),
    )
    await index.close()


def _upload(
    client: TestClient,
    org: SeedResult,
    name: str,
    data: bytes,
    acl: list[str],
    *,
    canonical_key: str | None = None,
    classification: str = "internal",
    expect: int = 201,
) -> Any:
    metadata = {
        "title": "Synthetic leave policy",
        "canonical_key": canonical_key or f"synthetic:{uuid.uuid4().hex[:8]}",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "classification": classification,
        "acl_subjects": acl,
    }
    response = client.post(
        "/api/v1/documents",
        headers={"X-Dev-User": "dev|chitra"},
        files={"file": (name, data)},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == expect, response.text
    return response.json()


def _search(client: TestClient, user: str, query: str) -> list[dict[str, Any]]:
    response = client.post(
        "/api/v1/search", headers={"X-Dev-User": f"dev|{user}"}, json={"query": query}
    )
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()["results"]
    return results


async def test_upload_is_ingested_and_found_only_by_permitted_users(
    client: TestClient,
    org: SeedResult,
    queue: RecordingQueue,
    deps: PipelineDeps,
    owner_engine: AsyncEngine,
) -> None:
    uploaded = _upload(client, org, "policy.docx", POLICY, [user_badge(org.users["asha"])])
    job_id = uuid.UUID(uploaded["ingestion_job_id"])
    assert queue.jobs == [(org.organisation_id, job_id)]  # handed to the worker

    status = await run_ingestion(deps, org.organisation_id, job_id)
    assert status is IngestionStatus.SUCCEEDED

    job = client.get(f"/api/v1/ingestion-jobs/{job_id}", headers={"X-Dev-User": "dev|chitra"})
    assert job.json()["status"] == "succeeded"
    assert job.json()["stage"] == "done"
    assert job.json()["chunk_count"] >= 2
    hidden = client.get(f"/api/v1/ingestion-jobs/{job_id}", headers={"X-Dev-User": "dev|ravi"})
    assert hidden.status_code == 404

    # asha is on the guest list: she finds it, in both languages.
    english = _search(client, "asha", "how many days of annual leave")
    assert english
    assert english[0]["document_id"] == uploaded["document_id"]
    assert {r["language"] for r in _search(client, "asha", "वार्षिक अवकाश")} >= {"hi"}
    # meera (same unit, not on the list), ravi (other unit) and kiran (expired grant): nothing.
    for user in ("meera", "ravi", "kiran", "nobody"):
        found = {r["document_id"] for r in _search(client, user, "annual leave days")}
        assert uploaded["document_id"] not in found, user

    # Every indexed point carries the guest list and tenant in its payload.
    async with owner_engine.connect() as conn:
        chunks = (
            await conn.execute(
                text("SELECT count(*) FROM chunk WHERE document_version_id = :v"),
                {"v": uploaded["version_id"]},
            )
        ).scalar_one()
    assert chunks == job.json()["chunk_count"]


async def test_stale_index_is_caught_by_the_database_recheck(
    client: TestClient, org: SeedResult, deps: PipelineDeps, owner_engine: AsyncEngine
) -> None:
    uploaded = _upload(client, org, "policy.docx", POLICY, [user_badge(org.users["asha"])])
    await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))
    assert _search(client, "asha", "annual leave")
    # The share expires; the vector index still lists asha, but nothing is returned.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE document_acl SET expires_at = now() - interval '1 minute'"
                " WHERE document_version_id = :v"
            ),
            {"v": uploaded["version_id"]},
        )
    assert _search(client, "asha", "annual leave") == []


async def test_a_search_without_permission_filters_is_refused(deps: PipelineDeps) -> None:
    await deps.index.ensure_collection()
    with pytest.raises(UnscopedSearchError):
        await deps.index.search([0.0] * 1024, {"must": []}, limit=5)


async def test_vectors_from_another_model_are_never_mixed_in(
    settings: Settings, deps: PipelineDeps
) -> None:
    await deps.index.ensure_collection()  # the test collection holds fake (1024) vectors
    other_model = build_vector_index(settings, dimensions=384)
    try:
        with pytest.raises(VectorIndexError, match="separate collection"):
            await other_model.ensure_collection()
    finally:
        await other_model.close()


async def test_a_file_without_text_fails_cleanly(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    uploaded = _upload(
        client, org, "scan.pdf", make_image_only_pdf(), [user_badge(org.users["asha"])]
    )
    job_id = uuid.UUID(uploaded["ingestion_job_id"])
    assert await run_ingestion(deps, org.organisation_id, job_id) is IngestionStatus.FAILED
    job = client.get(f"/api/v1/ingestion-jobs/{job_id}", headers={"X-Dev-User": "dev|chitra"})
    assert (job.json()["status"], job.json()["error_code"]) == ("failed", "no_text_layer")


async def test_an_outage_is_retried_and_only_the_last_attempt_fails(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    uploaded = _upload(client, org, "policy.docx", POLICY, [user_badge(org.users["asha"])])
    job_id = uuid.UUID(uploaded["ingestion_job_id"])
    down = QdrantIndex.connect(
        "http://127.0.0.1:9", "unused-key", "unused", FakeEmbedder.dimensions
    )
    broken = PipelineDeps(deps.session_factory, deps.store, down, deps.embedder)

    def job_state() -> tuple[str, str | None]:
        body = client.get(
            f"/api/v1/ingestion-jobs/{job_id}", headers={"X-Dev-User": "dev|chitra"}
        ).json()
        return body["status"], body["error_code"]

    try:
        with pytest.raises(TransientJobError):
            await run_ingestion(broken, org.organisation_id, job_id, final_attempt=False)
        assert job_state() == ("queued", "index_unavailable")  # a retry is coming
        with pytest.raises(TransientJobError):
            await run_ingestion(broken, org.organisation_id, job_id, final_attempt=True)
        assert job_state() == ("failed", "index_unavailable")
    finally:
        await down.close()
    # Once the index is back, the same job succeeds.
    assert await run_ingestion(deps, org.organisation_id, job_id) is IngestionStatus.SUCCEEDED


async def test_parallel_workers_can_create_a_new_collection_together(settings: Settings) -> None:
    fresh = settings.model_copy(
        update={"qdrant_collection": f"lekha_test_race_{uuid.uuid4().hex[:8]}"}
    )
    indexes = [build_vector_index(fresh, FakeEmbedder.dimensions) for _ in range(4)]
    try:
        await asyncio.gather(*(index.ensure_collection() for index in indexes))
    finally:
        await indexes[0]._client.delete(f"/collections/{fresh.qdrant_collection}")
        for index in indexes:
            await index.close()


# --- versions -------------------------------------------------------------------------

POLICY_V2 = make_docx(
    [("Annual leave", ["From next year employees receive thirty days of annual leave."])]
)
POLICY_V3 = make_docx(
    [("Annual leave", ["Employees receive thirty two days of annual leave from 2028."])]
)


async def _points(deps: PipelineDeps, **match: str) -> list[dict[str, Any]]:
    """Payloads of the points matching every key=value (straight from the vector index)."""
    response = await deps.index._client.post(
        f"/collections/{deps.index._collection}/points/scroll",
        json={
            "filter": {"must": [{"key": k, "match": {"value": v}} for k, v in match.items()]},
            "limit": 100,
            "with_payload": True,
        },
    )
    response.raise_for_status()
    return [p["payload"] for p in response.json()["result"]["points"]]


async def test_a_new_version_takes_over_only_once_it_is_indexed(
    client: TestClient, org: SeedResult, deps: PipelineDeps, owner_engine: AsyncEngine
) -> None:
    asha = [user_badge(org.users["asha"])]
    key = f"synthetic:{uuid.uuid4().hex[:8]}"
    v1 = _upload(client, org, "policy.docx", POLICY, asha, canonical_key=key)
    await run_ingestion(deps, org.organisation_id, uuid.UUID(v1["ingestion_job_id"]))

    v2 = _upload(client, org, "policy-v2.docx", POLICY_V2, asha, canonical_key=key)
    assert (v2["document_id"], v2["version_number"]) == (v1["document_id"], 2)
    # Not processed yet: readers still get version 1.
    assert {r["version_id"] for r in _search(client, "asha", "annual leave")} == {v1["version_id"]}

    status = await run_ingestion(deps, org.organisation_id, uuid.UUID(v2["ingestion_job_id"]))
    assert status is IngestionStatus.SUCCEEDED
    assert {r["version_id"] for r in _search(client, "asha", "annual leave")} == {v2["version_id"]}
    # The index itself was updated, not just filtered by the database re-check.
    old = await _points(deps, document_version_id=v1["version_id"])
    assert old  # kept: earlier versions stay addressable
    assert not any(p["is_current_version"] for p in old)
    new = await _points(deps, document_version_id=v2["version_id"])
    assert all(p["is_current_version"] for p in new)
    async with owner_engine.connect() as conn:
        superseded_by = (
            await conn.execute(
                text("SELECT superseded_by_version_id FROM document_version WHERE id = :v"),
                {"v": v1["version_id"]},
            )
        ).scalar_one()
    assert str(superseded_by) == v2["version_id"]


async def test_a_late_older_version_never_replaces_a_newer_one(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    asha = [user_badge(org.users["asha"])]
    key = f"synthetic:{uuid.uuid4().hex[:8]}"
    v1 = _upload(client, org, "a.docx", POLICY, asha, canonical_key=key)
    await run_ingestion(deps, org.organisation_id, uuid.UUID(v1["ingestion_job_id"]))
    v2 = _upload(client, org, "b.docx", POLICY_V2, asha, canonical_key=key)
    v3 = _upload(client, org, "c.docx", POLICY_V3, asha, canonical_key=key)
    await run_ingestion(deps, org.organisation_id, uuid.UUID(v3["ingestion_job_id"]))
    await run_ingestion(deps, org.organisation_id, uuid.UUID(v2["ingestion_job_id"]))  # late

    document = client.get(
        f"/api/v1/documents/{v1['document_id']}", headers={"X-Dev-User": "dev|asha"}
    ).json()
    assert document["version_number"] == 3
    assert {r["version_id"] for r in _search(client, "asha", "annual leave")} == {v3["version_id"]}
    late = await _points(deps, document_version_id=v2["version_id"])
    assert not any(p["is_current_version"] for p in late)


def test_a_new_version_cannot_change_who_may_read_the_old_ones(
    client: TestClient, org: SeedResult
) -> None:
    asha = [user_badge(org.users["asha"])]
    key = f"synthetic:{uuid.uuid4().hex[:8]}"
    _upload(client, org, "policy.docx", POLICY, asha, canonical_key=key)
    refused = _upload(
        client,
        org,
        "policy.docx",
        POLICY,
        asha,
        canonical_key=key,
        classification="public",
        expect=409,
    )
    assert refused["error"]["code"] == "conflict"


# --- deletion -------------------------------------------------------------------------


def _delete(client: TestClient, user: str, document_id: str) -> Any:
    return client.post(
        "/api/v1/deletion-requests",
        headers={"X-Dev-User": f"dev|{user}"},
        json={"scope": "document", "document_id": document_id},
    )


async def test_deleting_a_document_hides_it_at_once_then_purges_everything(
    client: TestClient,
    org: SeedResult,
    queue: RecordingQueue,
    deps: PipelineDeps,
    owner_engine: AsyncEngine,
) -> None:
    asha = [user_badge(org.users["asha"])]
    key = f"synthetic:{uuid.uuid4().hex[:8]}"
    uploaded = _upload(client, org, "policy.docx", POLICY, asha, canonical_key=key)
    document_id = uploaded["document_id"]
    await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))
    assert _search(client, "asha", "annual leave")

    assert _delete(client, "asha", document_id).status_code == 403  # reader, not curator
    assert _delete(client, "ravi", document_id).status_code == 404  # can't even see it
    response = _delete(client, "chitra", document_id)
    assert response.status_code == 202, response.text
    request_id = uuid.UUID(response.json()["id"])
    assert response.json()["status"] == "pending"
    assert queue.purges == [(org.organisation_id, request_id)]

    # Hidden immediately, before the purge has run.
    assert _search(client, "asha", "annual leave") == []
    opened = client.get(f"/api/v1/documents/{document_id}", headers={"X-Dev-User": "dev|asha"})
    assert opened.status_code == 404

    assert await run_purge(deps, org.organisation_id, request_id) is DeletionStatus.COMPLETED
    status = client.get(
        f"/api/v1/deletion-requests/{request_id}", headers={"X-Dev-User": "dev|chitra"}
    ).json()
    assert status["status"] == "completed"
    assert status["chunks_removed"] >= 2
    assert status["files_removed"] == 1
    hidden = client.get(
        f"/api/v1/deletion-requests/{request_id}", headers={"X-Dev-User": "dev|asha"}
    )
    assert hidden.status_code == 404

    # Derived data is gone; the tombstone and the audit trail remain.
    assert await _points(deps, document_id=document_id) == []
    params = {"d": document_id}
    async with owner_engine.connect() as conn:
        chunks = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM chunk c JOIN document_version v"
                    " ON v.id = c.document_version_id WHERE v.document_id = :d"
                ),
                params,
            )
        ).scalar_one()
        storage_key = (
            await conn.execute(
                text(
                    "SELECT f.storage_key FROM document_file f JOIN document_version v"
                    " ON v.file_id = f.id WHERE v.document_id = :d"
                ),
                params,
            )
        ).scalar_one()
        deleted_at = (
            await conn.execute(text("SELECT deleted_at FROM document WHERE id = :d"), params)
        ).scalar_one()
        audited = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM audit_event WHERE target_id = :d"
                    " AND path = '/api/v1/deletion-requests' AND status_code = 202"
                ),
                params,
            )
        ).scalar_one()
    assert chunks == 0
    assert deleted_at is not None
    assert audited == 1
    with pytest.raises(ObjectStoreError):
        await deps.store.get(storage_key)

    # Gone for good: no second deletion, and the key can't bring it back.
    assert _delete(client, "chitra", document_id).status_code == 404
    _upload(client, org, "policy.docx", POLICY, asha, canonical_key=key, expect=409)


async def test_a_purge_survives_an_outage(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    uploaded = _upload(client, org, "policy.docx", POLICY, [user_badge(org.users["asha"])])
    await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))
    request_id = uuid.UUID(_delete(client, "chitra", uploaded["document_id"]).json()["id"])
    down = QdrantIndex.connect(
        "http://127.0.0.1:9", "unused-key", "unused", FakeEmbedder.dimensions
    )
    broken = PipelineDeps(deps.session_factory, deps.store, down, deps.embedder)
    try:
        with pytest.raises(TransientJobError):
            await run_purge(broken, org.organisation_id, request_id, final_attempt=False)
    finally:
        await down.close()
    assert await run_purge(deps, org.organisation_id, request_id) is DeletionStatus.COMPLETED
    assert await _points(deps, document_id=uploaded["document_id"]) == []


async def test_a_document_deleted_before_processing_is_never_indexed(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    uploaded = _upload(client, org, "policy.docx", POLICY, [user_badge(org.users["asha"])])
    assert _delete(client, "chitra", uploaded["document_id"]).status_code == 202
    status = await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))
    assert status is IngestionStatus.FAILED
    assert await _points(deps, document_id=uploaded["document_id"]) == []


# --- hybrid retrieval -----------------------------------------------------------------


def _coded_policy(code: str) -> bytes:
    return make_docx([("Parking", [f"Parking permits are renewed at reception. Form {code}."])])


async def test_keyword_search_finds_exact_codes_and_obeys_permissions(
    client: TestClient, org: SeedResult, deps: PipelineDeps, settings: Settings
) -> None:
    asha_doc = _upload(
        client, org, "a.docx", _coded_policy("PRK-7781"), [user_badge(org.users["asha"])]
    )
    meera_doc = _upload(
        client, org, "m.docx", _coded_policy("PRK-9932"), [user_badge(org.users["meera"])]
    )
    for uploaded in (asha_doc, meera_doc):
        await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))

    assert settings.retrieval_mode == "hybrid"
    found = [r["document_id"] for r in _search(client, "asha", "PRK-7781")]
    assert found[0] == asha_doc["document_id"]
    # The exact code of a document asha may not read finds nothing of it, by either path.
    probe = {r["document_id"] for r in _search(client, "asha", "PRK-9932")}
    assert meera_doc["document_id"] not in probe
    assert meera_doc["document_id"] in {
        r["document_id"] for r in _search(client, "meera", "PRK-9932")
    }
    for user in ("ravi", "kiran", "nobody"):
        assert _search(client, user, "PRK-7781 PRK-9932 parking") == [], user


@pytest.mark.parametrize("query", ["' | ! & :*", "&&&", "(((", "'; DROP TABLE chunk; --"])
def test_search_syntax_in_a_question_is_harmless(
    client: TestClient, org: SeedResult, query: str
) -> None:
    response = client.post(
        "/api/v1/search", headers={"X-Dev-User": "dev|asha"}, json={"query": query}
    )
    assert response.status_code == 200, response.text
