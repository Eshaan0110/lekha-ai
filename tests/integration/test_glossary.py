"""The shared glossary end to end: who manages and sees entries, and what they do to search."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.api.ratelimit import RateDecision
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.wiring import build_vector_index
from apps.worker.pipeline import PipelineDeps, run_ingestion
from db.engine import create_session_factory
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory.s3_store import S3ObjectStore
from model_layer import FakeEmbedder
from tests.fixtures.fake_llm import FakeLanguageModel
from tests.fixtures.synthetic_docs import make_docx
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

KANNADA_LEAVE = make_docx([("ಸಾಂದರ್ಭಿಕ ರಜೆ", ["ನೌಕರರಿಗೆ ಪ್ರತಿ ವರ್ಷ 18 ದಿನಗಳ ಸಾಂದರ್ಭಿಕ ರಜೆ ಸಿಗುತ್ತದೆ."])])


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
def model() -> FakeLanguageModel:
    return FakeLanguageModel()


@pytest.fixture
def client(settings: Settings, org: SeedResult, model: FakeLanguageModel) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    app = create_app(
        app_settings, job_queue=NullQueue(), language_model=model, ask_limiter=AllowAll()
    )
    with TestClient(app) as test_client:
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


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _add(client: TestClient, org: SeedResult, user: str = "chitra", **fields: Any) -> Any:
    body = {
        "canonical": "Casual leave",
        "aliases": ["CL", "ಸಾಂದರ್ಭಿಕ ರಜೆ"],
        "description": "short leave for personal reasons",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        **fields,
    }
    return client.post("/api/v1/glossary", headers=_as(user), json=body)


def _names(client: TestClient, user: str) -> set[str]:
    return {e["canonical"] for e in client.get("/api/v1/glossary", headers=_as(user)).json()}


def test_curators_manage_their_units_glossary_and_its_members_see_it(
    client: TestClient, org: SeedResult
) -> None:
    assert _add(client, org, "asha").status_code == 403  # a viewer, not a curator
    assert _add(client, org, "bina").status_code == 403  # a curator, but of Beta
    created = _add(client, org)
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["aliases"] == ["CL", "ಸಾಂದರ್ಭಿಕ ರಜೆ"]

    for user in ("asha", "meera", "chitra"):
        assert _names(client, user) == {"Casual leave"}, user
    for user in ("ravi", "bina", "nobody", "kiran"):
        assert _names(client, user) == set(), user

    url = f"/api/v1/glossary/{entry['id']}"
    change = {"canonical": "Casual leave", "aliases": ["CL"], "description": "", "shared": True}
    assert client.patch(url, headers=_as("asha"), json=change).status_code == 403
    changed = client.patch(url, headers=_as("chitra"), json=change)
    assert changed.status_code == 200
    assert _names(client, "ravi") == {"Casual leave"}  # shared with the whole organisation
    assert client.delete(url, headers=_as("bina")).status_code == 403
    assert client.delete(url, headers=_as("chitra")).status_code == 204
    assert _names(client, "asha") == set()


def test_a_name_means_one_thing(client: TestClient, org: SeedResult) -> None:
    assert _add(client, org).status_code == 201
    clash = _add(client, org, canonical="Compensatory leave", aliases=["  cl "])
    assert clash.status_code == 409
    repeats = _add(
        client, org, canonical="Earned leave", aliases=["EL", "el", "Earned Leave"]
    ).json()
    assert repeats["aliases"] == ["EL"]  # repeats dropped, compared ignoring case and spaces


async def test_an_english_question_finds_a_kannada_document_through_the_glossary(
    client: TestClient, org: SeedResult, deps: PipelineDeps, model: FakeLanguageModel
) -> None:
    metadata = {
        "title": "ಸಾಂದರ್ಭಿಕ ರಜೆ",
        "canonical_key": "synthetic:casual-leave-kn",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "classification": "internal",
        "acl_subjects": [user_badge(org.users["asha"])],
    }
    uploaded = client.post(
        "/api/v1/documents",
        headers=_as("chitra"),
        files={"file": ("cl.docx", KANNADA_LEAVE)},
        data={"metadata": json.dumps(metadata)},
    ).json()
    await run_ingestion(deps, org.organisation_id, uuid.UUID(uploaded["ingestion_job_id"]))
    # An English policy that shares words with the question ("days", "leave").
    english = client.post(
        "/api/v1/documents",
        headers=_as("chitra"),
        files={
            "file": (
                "al.docx",
                make_docx([("Annual leave", ["Staff get 24 days of annual leave."])]),
            )
        },
        data={
            "metadata": json.dumps(
                {**metadata, "title": "Annual leave", "canonical_key": "synthetic:al"}
            )
        },
    ).json()
    await run_ingestion(deps, org.organisation_id, uuid.UUID(english["ingestion_job_id"]))

    def search(user: str) -> dict[str, Any]:
        response = client.post(
            "/api/v1/search", headers=_as(user), json={"query": "casual leave days"}
        )
        body: dict[str, Any] = response.json()
        return body

    before = search("asha")
    assert before["results"][0]["document_id"] == english["document_id"]  # wrong document first
    assert before["also_searched"] == []

    assert _add(client, org).status_code == 201
    after = search("asha")
    assert after["also_searched"] == ["CL", "ಸಾಂದರ್ಭಿಕ ರಜೆ"]
    assert after["results"][0]["document_id"] == uploaded["document_id"]

    # The answer model is told which names mean the same thing.
    client.post(
        "/api/v1/ask", headers=_as("asha"), json={"question": "How many days of casual leave?"}
    )
    assert "Glossary (names that mean the same thing):" in model.prompts[-1]
    assert "Casual leave = CL = ಸಾಂದರ್ಭಿಕ ರಜೆ" in model.prompts[-1]

    # Ravi can't see Alpha's glossary entry: no expansion, and of course no document.
    ravi = search("ravi")
    assert ravi["also_searched"] == []
    assert ravi["results"] == []
