"""Phase 2 acceptance on the synthetic corpus: 100 documents indexed end to end, and a
50-question permission red-team with zero anomalies.

An anomaly is any search result the asker may not read, judged against the hand-written
answer key in ``tests.fixtures.corpus`` (not against the code under test). Some cases are
left deliberately stale in the vector index (shares expired, documents deleted but not yet
purged) so the database re-check is exercised too. A recall check proves the searches
return real results, so "zero anomalies" can't come from an empty index.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.wiring import build_embedder, build_vector_index
from apps.worker.pipeline import PipelineDeps, run_ingestion
from apps.worker.purge import run_purge
from db.engine import create_session_factory
from db.schema import DeletionStatus, IngestionStatus
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory.s3_store import S3ObjectStore
from tests.fixtures.corpus import USERS, After, CorpusDoc, build_corpus, red_team_queries
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration


class NullQueue:
    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


BGE_M3_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-m3"


@pytest.fixture(
    params=[
        "fake",
        pytest.param(
            "bge-m3",
            marks=pytest.mark.skipif(
                not (BGE_M3_DIR / "onnx" / "model.onnx_data").is_file(),
                reason="BGE-M3 not downloaded",
            ),
        ),
    ]
)
def run_settings(request: pytest.FixtureRequest, settings: Settings) -> Settings:
    """The red team runs with the fake embedder always, and with real BGE-M3 when present.
    Each model gets its own collection: vectors from different models can't be compared."""
    embedder: str = request.param
    if embedder == "fake":
        return settings
    return settings.model_copy(
        update={
            "embedder": embedder,
            "embedder_model_dir": BGE_M3_DIR,
            "qdrant_collection": f"{settings.qdrant_collection}_bge_m3",
        }
    )


@pytest.fixture
def client(run_settings: Settings, org: SeedResult) -> Iterator[TestClient]:
    app_settings = run_settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings, job_queue=NullQueue())) as test_client:
        yield test_client


@pytest.fixture
async def deps(run_settings: Settings, app_engine: AsyncEngine) -> AsyncIterator[PipelineDeps]:
    embedder = build_embedder(run_settings)
    index = build_vector_index(run_settings, embedder.dimensions)
    yield PipelineDeps(
        session_factory=create_session_factory(app_engine),
        store=S3ObjectStore.from_settings(run_settings),
        index=index,
        embedder=embedder,
    )
    await index.close()


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _guest_keys(doc: CorpusDoc, org: SeedResult) -> list[str]:
    return [
        user_badge(org.users[g.removeprefix("user:")])
        if g.startswith("user:")
        else org.role_badges[g]
        for g in doc.pattern.guests
    ]


def _upload(client: TestClient, org: SeedResult, doc: CorpusDoc) -> dict[str, Any]:
    metadata = {
        "title": doc.title,
        "canonical_key": doc.canonical_key,
        "business_unit_id": str(org.business_units[doc.pattern.business_unit]),
        "classification": doc.pattern.classification,
        "acl_subjects": _guest_keys(doc, org),
    }
    response = client.post(
        "/api/v1/documents",
        headers=_as(doc.pattern.uploader),
        files={"file": (doc.filename, doc.data)},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 201, (doc.code, response.text)
    body: dict[str, Any] = response.json()
    return body


def _search(client: TestClient, user: str, query: str) -> list[dict[str, Any]]:
    response = client.post("/api/v1/search", headers=_as(user), json={"query": query, "limit": 20})
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()["results"]
    return results


async def test_100_documents_and_a_50_question_red_team_with_zero_anomalies(
    client: TestClient,
    org: SeedResult,
    deps: PipelineDeps,
    owner_engine: AsyncEngine,
    run_settings: Settings,
) -> None:
    corpus = build_corpus()
    uploads = {doc.code: _upload(client, org, doc) for doc in corpus}
    by_document_id = {uploads[d.code]["document_id"]: d for d in corpus}

    # Raised to confidential before processing, as if uploaded with the release gate open.
    confidential = [
        uploads[d.code]["document_id"] for d in corpus if d.pattern.after is After.CONFIDENTIAL
    ]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE document SET classification = 'confidential' WHERE id = ANY(:ids)"),
            {"ids": confidential},
        )

    # 1. Every document is processed end to end.
    for doc in corpus:
        job_id = uuid.UUID(uploads[doc.code]["ingestion_job_id"])
        status = await run_ingestion(deps, org.organisation_id, job_id)
        assert status is IngestionStatus.SUCCEEDED, doc.code
    async with owner_engine.connect() as conn:
        succeeded = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM ingestion_job WHERE organisation_id = :o"
                    " AND status = 'succeeded' AND chunk_count > 0"
                ),
                {"o": org.organisation_id},
            )
        ).scalar_one()
        rows = await conn.execute(
            text(
                "SELECT language, count(*) FROM chunk WHERE organisation_id = :o GROUP BY language"
            ),
            {"o": org.organisation_id},
        )
        languages = {language: count for language, count in rows}
    assert succeeded == 100
    assert languages.keys() == {"en", "kn"}

    # 2. Changes after indexing, left stale in the vector index on purpose.
    expired = [
        uploads[d.code]["version_id"] for d in corpus if d.pattern.after is After.EXPIRE_SHARE
    ]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE document_acl SET expires_at = :t WHERE document_version_id = ANY(:ids)"),
            {"t": datetime.now(UTC) - timedelta(days=1), "ids": expired},
        )
    deleted = [d for d in corpus if d.pattern.after is After.DELETE]
    for i, doc in enumerate(deleted):
        response = client.post(
            "/api/v1/deletion-requests",
            headers=_as(doc.pattern.uploader),
            json={"scope": "document", "document_id": uploads[doc.code]["document_id"]},
        )
        assert response.status_code == 202, response.text
        if i % 2 == 0:  # purge half; the other half stay in the index until purged
            request_id = uuid.UUID(response.json()["id"])
            assert (
                await run_purge(deps, org.organisation_id, request_id) is DeletionStatus.COMPLETED
            )

    # 3. The red team: every question, asked by every user.
    queries = red_team_queries(corpus)
    assert len(queries) == 50
    anomalies: list[tuple[str, str, str]] = []
    searches = hits = 0
    for user in USERS:
        for query in queries:
            searches += 1
            for result in _search(client, user, query):
                hits += 1
                doc = by_document_id[result["document_id"]]
                if user not in doc.pattern.readers:
                    anomalies.append((user, query, doc.code))
    assert anomalies == [], f"{len(anomalies)} permission anomalies: {anomalies[:10]}"
    assert searches == 350
    assert hits > 0

    # 4. Recall: every reader finds every document they may read, by its reference code.
    # Dense-only BGE-M3 managed 108 of 124 (87%), because meaning search is weak at exact
    # codes; hybrid retrieval (keyword + meaning, the default) finds all 124 (2026-10-21).
    pairs = sum(len(doc.pattern.readers) for doc in corpus)
    missed = [
        (user, doc.code)
        for doc in corpus
        for user in sorted(doc.pattern.readers)
        if uploads[doc.code]["document_id"]
        not in {r["document_id"] for r in _search(client, user, doc.code)}
    ]
    assert missed == [], f"{len(missed)}/{pairs} missed: {missed[:10]}"
