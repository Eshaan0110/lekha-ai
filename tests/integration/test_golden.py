"""The golden-set evaluation, run end to end through the real API.

By default it uses the fake embedder and the fake extractive model: fast, deterministic,
and it enforces the hard rule (0 permission violations) on every integration run.

For the real measurement (slow: about 30 s per answer on the development laptop):

    $env:LEKHA_EVAL_EMBEDDER = "bge-m3"; $env:LEKHA_EVAL_LLM = "ollama"
    $env:LEKHA_EVAL_REPORT = "docs/eval/golden-ollama-bge-m3.md"
    uv run pytest -m integration tests/integration/test_golden.py -s

Quality thresholds (accuracy, citations, abstention) are reported, not asserted: they are
the Phase 3 acceptance evidence, read from the report.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.api.ratelimit import RateDecision
from apps.scripts.demo_corpus import GLOSSARY, MEETINGS, DemoMeeting
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.wiring import build_embedder, build_language_model, build_vector_index
from apps.worker.pipeline import PipelineDeps, run_ingestion
from db.engine import create_session_factory
from db.schema import IngestionStatus
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory.s3_store import S3ObjectStore
from model_layer import LanguageModel
from tests.eval.golden import DOCS, QUESTIONS, GoldenDoc
from tests.eval.harness import QuestionResult, check_safety, report_markdown, score
from tests.fixtures.fake_llm import FakeLanguageModel
from tests.fixtures.synthetic_docs import make_docx
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
EMBEDDER = os.environ.get("LEKHA_EVAL_EMBEDDER", "fake")
LLM = os.environ.get("LEKHA_EVAL_LLM", "fake")


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


class Unlimited:
    async def hit(self, scope: str, user_id: uuid.UUID) -> RateDecision:
        return RateDecision(allowed=True, limit=1000, remaining=1000, retry_after=60)


@pytest.fixture
def eval_settings(settings: Settings) -> Settings:
    update: dict[str, Any] = {"embedder": EMBEDDER, "llm": "none"}  # Any: mixed settings
    if EMBEDDER != "fake":
        update["embedder_model_dir"] = ROOT / "models" / EMBEDDER
        update["qdrant_collection"] = (
            f"{settings.qdrant_collection}_golden_{EMBEDDER.replace('-', '_')}"
        )
    if LLM == "ollama":
        update["llm"] = "ollama"
        update["llm_timeout_seconds"] = 300.0
    return settings.model_copy(update=update)


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def model(eval_settings: Settings) -> LanguageModel:
    built = build_language_model(eval_settings)
    return built if built is not None else FakeLanguageModel()


@pytest.fixture
def client(eval_settings: Settings, org: SeedResult, model: LanguageModel) -> Iterator[TestClient]:
    app_settings = eval_settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    app = create_app(
        app_settings, job_queue=NullQueue(), language_model=model, ask_limiter=Unlimited()
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def deps(eval_settings: Settings, app_engine: AsyncEngine) -> AsyncIterator[PipelineDeps]:
    embedder = build_embedder(eval_settings)
    index = build_vector_index(eval_settings, embedder.dimensions)
    yield PipelineDeps(
        session_factory=create_session_factory(app_engine),
        store=S3ObjectStore.from_settings(eval_settings),
        index=index,
        embedder=embedder,
    )
    await index.close()


def _guests(doc: GoldenDoc, org: SeedResult) -> list[str]:
    if doc.readers == "meera":
        return [user_badge(org.users["meera"])]
    return [org.role_badges["viewer@" + doc.business_unit]]


def _upload(client: TestClient, org: SeedResult, doc: GoldenDoc) -> dict[str, Any]:
    metadata = {
        "title": doc.title,
        "canonical_key": f"synthetic:golden-{doc.key.lower()}",
        "business_unit_id": str(org.business_units[doc.business_unit]),
        "classification": "internal",
        "acl_subjects": _guests(doc, org),
    }
    uploader = "bina" if doc.business_unit == "bu-beta" else "chitra"
    response = client.post(
        "/api/v1/documents",
        headers={"X-Dev-User": f"dev|{uploader}"},
        files={"file": (f"{doc.key}.docx", make_docx([(doc.heading, list(doc.sentences))]))},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 201, (doc.key, response.text)
    body: dict[str, Any] = response.json()
    return body


def _import_meeting(client: TestClient, org: SeedResult, meeting: DemoMeeting) -> dict[str, Any]:
    organiser = meeting.organiser
    unit = "bu-beta" if organiser == "ravi" else "bu-alpha"
    metadata = {
        "title": meeting.title,
        "business_unit_id": str(org.business_units[unit]),
        "started_at": meeting.started_at,
        "participant_user_ids": [str(org.users[p]) for p in meeting.participants],
        "external_id": f"golden:{meeting.key}",
    }
    response = client.post(
        "/api/v1/meetings",
        headers={"X-Dev-User": f"dev|{organiser}"},
        files={"file": (meeting.filename, meeting.transcript.encode("utf-8"))},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 201, (meeting.key, response.text)
    body: dict[str, Any] = response.json()
    return body


async def test_golden_set(
    client: TestClient,
    org: SeedResult,
    deps: PipelineDeps,
    eval_settings: Settings,
    model: LanguageModel,
    tmp_path: Path,
) -> None:
    uploads = {doc.key: _upload(client, org, doc) for doc in DOCS}
    for key, uploaded in uploads.items():
        status = await run_ingestion(
            deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"])
        )
        assert status is IngestionStatus.SUCCEEDED, key
    for meeting in MEETINGS:
        imported = _import_meeting(client, org, meeting)
        status = await run_ingestion(
            deps, org.organisation_id, uuid.UUID(imported["ingestion_job_id"])
        )
        assert status is IngestionStatus.SUCCEEDED, meeting.key
        uploads[f"MT-{meeting.key}"] = imported
    for unit, curator, shared, canonical, aliases, description in GLOSSARY:
        added = client.post(
            "/api/v1/glossary",
            headers={"X-Dev-User": f"dev|{curator}"},
            json={
                "business_unit_id": str(org.business_units[unit]),
                "canonical": canonical,
                "aliases": list(aliases),
                "description": description,
                "shared": shared,
            },
        )
        assert added.status_code == 201, added.text
    key_by_document = {u["document_id"]: key for key, u in uploads.items()}

    results: list[QuestionResult] = []
    model_errors: list[str] = []
    for question in QUESTIONS:
        headers = {"X-Dev-User": f"dev|{question.asker}"}
        found = client.post(
            "/api/v1/search", headers=headers, json={"query": question.question, "limit": 10}
        ).json()["results"]
        started = time.monotonic()
        response = client.post("/api/v1/ask", headers=headers, json={"question": question.question})
        if response.status_code == 503 and LLM != "fake":
            # A one-off local model hiccup shouldn't throw away hours of results: retry once,
            # then record it as an error (counted and reported, scored as an abstention).
            await asyncio.sleep(10)
            started = time.monotonic()
            response = client.post(
                "/api/v1/ask", headers=headers, json={"question": question.question}
            )
        latency = time.monotonic() - started
        body: dict[str, Any]
        if response.status_code == 503 and LLM != "fake":
            model_errors.append(question.id)
            body = {
                "abstained": True,
                "abstention_reason": "model_error",
                "answer": "",
                "citations": [],
            }
        else:
            assert response.status_code == 200, (question.id, response.text)
            body = response.json()
        result = QuestionResult(
            question=question,
            abstained=body["abstained"],
            reason=body["abstention_reason"],
            answer=body["answer"],
            cited=[key_by_document.get(c["document_id"], "?") for c in body["citations"]],
            spans=[c["span"] for c in body["citations"]],
            retrieved=[key_by_document.get(r["document_id"], "?") for r in found],
            latency_s=latency,
        )
        check_safety(result)
        results.append(result)

    board = score(results)
    config = {
        "embedder": eval_settings.embedder,
        "language model": model.model_name,
        "retrieval": eval_settings.retrieval_mode,
        "sources per question": str(eval_settings.ask_top_k),
        "model errors (scored as abstentions)": ", ".join(model_errors) or "none",
        "date": time.strftime("%Y-%m-%d"),
    }
    report = report_markdown(board, results, config)
    target = os.environ.get("LEKHA_EVAL_REPORT")
    path = ROOT / target if target else tmp_path / "golden.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    summary = {k: v for k, v in vars(board).items() if k != "counts"}
    print("golden report:", path)  # ASCII only: Windows consoles can't show the report
    print(summary)

    # The hard rule holds for every model: nothing the asker may not read ever reaches them.
    assert board.permission_violations == 0, [r.leaks for r in results if r.leaks]
    assert board.counts["questions"] == len(QUESTIONS)
    assert len(model_errors) <= len(QUESTIONS) // 20, model_errors  # more means a broken run
