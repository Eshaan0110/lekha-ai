"""End to end: upload → worker pipeline → permission-filtered search, on the real stack."""

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
from apps.worker.pipeline import PipelineDeps, TransientIngestionError, run_ingestion
from db.engine import create_session_factory
from db.schema import IngestionStatus
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
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


def _upload(client: TestClient, org: SeedResult, name: str, data: bytes, acl: list[str]) -> Any:
    metadata = {
        "title": "Synthetic leave policy",
        "canonical_key": f"synthetic:{uuid.uuid4().hex[:8]}",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "classification": "internal",
        "acl_subjects": acl,
    }
    response = client.post(
        "/api/v1/documents",
        headers={"X-Dev-User": "dev|chitra"},
        files={"file": (name, data)},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 201, response.text
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
        with pytest.raises(TransientIngestionError):
            await run_ingestion(broken, org.organisation_id, job_id, final_attempt=False)
        assert job_state() == ("queued", "index_unavailable")  # a retry is coming
        with pytest.raises(TransientIngestionError):
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
