"""Meeting memory end to end: import, participant-only access, versions, answers, deletion."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.api.ratelimit import RateDecision
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.wiring import build_vector_index
from apps.worker.pipeline import PipelineDeps, run_ingestion
from apps.worker.purge import run_purge
from db.engine import create_session_factory
from db.schema import IngestionStatus
from lekha.core.config import AuthMode, Settings
from lekha.memory.s3_store import S3ObjectStore
from model_layer import FakeEmbedder
from tests.fixtures.fake_llm import Behaviour, FakeLanguageModel
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

SAFETY_REVIEW = """WEBVTT

1
00:00:05.000 --> 00:00:09.000
<v Asha>Let's review the forklift incident from last week.</v>

2
00:12:40.000 --> 00:12:47.500
<v Meera>We agreed that all forklift drivers are retrained by the end of November.</v>
"""
STARTED = "2026-10-14T10:00:00+05:30"


class NullQueue:
    def __init__(self) -> None:
        self.reindexes: list[uuid.UUID] = []

    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return None

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        self.reindexes.append(job_id)

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
def queue() -> NullQueue:
    return NullQueue()


@pytest.fixture
def client(settings: Settings, org: SeedResult, queue: NullQueue) -> Iterator[TestClient]:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    app = create_app(
        app_settings,
        job_queue=queue,
        language_model=FakeLanguageModel(),
        ask_limiter=AllowAll(),
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


def _import(
    client: TestClient,
    org: SeedResult,
    user: str,
    *,
    participants: list[str],
    filename: str = "safety-review.vtt",
    transcript: str = SAFETY_REVIEW,
    **extra: Any,
) -> Any:
    metadata = {
        "title": "Weekly safety review",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "started_at": STARTED,
        "participant_user_ids": [str(org.users[p]) for p in participants],
        **extra,
    }
    return client.post(
        "/api/v1/meetings",
        headers=_as(user),
        files={"file": (filename, transcript.encode())},
        data={"metadata": json.dumps(metadata)},
    )


async def _imported(
    client: TestClient, org: SeedResult, deps: PipelineDeps, user: str, **kwargs: Any
) -> dict[str, Any]:
    response = _import(client, org, user, **kwargs)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    status = await run_ingestion(deps, org.organisation_id, uuid.UUID(body["ingestion_job_id"]))
    assert status is IngestionStatus.SUCCEEDED
    return body


def _search(client: TestClient, user: str, query: str) -> list[dict[str, Any]]:
    response = client.post("/api/v1/search", headers=_as(user), json={"query": query})
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()["results"]
    return results


async def test_only_participants_can_find_read_or_ask_about_a_meeting(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    meeting = await _imported(client, org, deps, "asha", participants=["meera"])
    assert meeting["speakers"] == ["Asha", "Meera"]
    assert meeting["turns"] == 2

    for user in ("asha", "meera"):
        hits = [
            r
            for r in _search(client, user, "forklift drivers retrained")
            if r["document_id"] == meeting["document_id"]
        ]
        assert hits, user
        assert hits[0]["kind"] == "meeting"
        assert hits[0]["start_ms"] == 5000
        listed = client.get("/api/v1/meetings", headers=_as(user)).json()
        assert [m["id"] for m in listed] == [meeting["meeting_id"]]

    # chitra curates Alpha but wasn't in the meeting; ravi, kiran and nobody even less so.
    for user in ("chitra", "ravi", "kiran", "nobody"):
        found = {r["document_id"] for r in _search(client, user, "forklift drivers retrained")}
        assert meeting["document_id"] not in found, user
        assert client.get("/api/v1/meetings", headers=_as(user)).json() == [], user
        detail = client.get(f"/api/v1/meetings/{meeting['meeting_id']}", headers=_as(user))
        assert detail.status_code == 404, user
        answer = client.post(
            "/api/v1/ask",
            headers=_as(user),
            json={"question": "When are forklift drivers retrained?"},
        ).json()
        assert "November" not in answer["answer"], user


async def test_answers_cite_the_meeting_and_the_moment(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    meeting = await _imported(client, org, deps, "asha", participants=["meera"])
    answer = client.post(
        "/api/v1/ask",
        headers=_as("meera"),
        json={"question": "By when are all forklift drivers retrained?"},
    ).json()
    assert answer["abstained"] is False, answer
    [citation] = answer["citations"]
    assert citation["document_id"] == meeting["document_id"]
    assert citation["kind"] == "meeting"
    assert citation["start_ms"] == 760_000  # 12:40, the turn that contains the quote
    assert "end of November" in citation["span"]

    detail = client.get(f"/api/v1/meetings/{meeting['meeting_id']}", headers=_as("meera")).json()
    assert detail["participants"] == ["Asha (synthetic)", "Meera (synthetic)"]
    assert [(t["at"], t["speaker"]) for t in detail["transcript"]] == [
        ("00:05", "Asha"),
        ("12:40", "Meera"),
    ]


async def test_importing_the_same_meeting_again_adds_a_version(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    first = await _imported(
        client, org, deps, "asha", participants=["meera"], external_id="teams:abc-123"
    )
    # Same source ID, corrected transcript: version 2 of the same meeting.
    corrected = SAFETY_REVIEW.replace("end of November", "15 November")
    second = _import(
        client,
        org,
        "asha",
        participants=["meera"],
        transcript=corrected,
        external_id="teams:abc-123",
    ).json()
    assert (second["meeting_id"], second["version_number"]) == (first["meeting_id"], 2)
    # Exactly the same file again: refused, not duplicated.
    again = _import(
        client,
        org,
        "asha",
        participants=["meera"],
        transcript=corrected,
        external_id="teams:abc-123",
    )
    assert again.status_code == 409
    # No source ID, same organiser, start within two minutes: still the same meeting.
    nearby = _import(
        client,
        org,
        "asha",
        participants=["meera"],
        transcript=corrected + "\n3\n00:20:00.000 --> 00:20:05.000\n<v Asha>Thanks all.</v>\n",
        started_at="2026-10-14T10:01:30+05:30",
    ).json()
    assert (nearby["meeting_id"], nearby["version_number"]) == (first["meeting_id"], 3)
    # Someone else's meeting at the same time is a different meeting.
    other = _import(client, org, "meera", participants=["asha"]).json()
    assert other["meeting_id"] != first["meeting_id"]


@pytest.mark.parametrize(
    ("user", "kwargs", "status"),
    [
        ("ravi", {}, 403),  # no role in Alpha
        ("asha", {"classification": "confidential"}, 403),  # release gate
        ("asha", {"filename": "minutes.pdf"}, 415),
        ("asha", {"transcript": "00:00:01.000 --> 00:00:02.000\nno header"}, 422),
        ("asha", {"started_at": "2026-10-14T10:00:00"}, 422),  # no timezone
    ],
)
def test_import_rules(
    client: TestClient, org: SeedResult, user: str, kwargs: dict[str, Any], status: int
) -> None:
    assert _import(client, org, user, participants=["meera"], **kwargs).status_code == status


def test_participants_must_be_lekha_users(client: TestClient, org: SeedResult) -> None:
    metadata = {
        "title": "Weekly safety review",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "started_at": STARTED,
        "participant_user_ids": [str(uuid.uuid4())],
    }
    response = client.post(
        "/api/v1/meetings",
        headers=_as("asha"),
        files={"file": ("m.vtt", SAFETY_REVIEW.encode())},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 422


async def test_the_organiser_can_delete_their_meeting_but_other_participants_cannot(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    meeting = await _imported(client, org, deps, "asha", participants=["meera"])

    def delete(user: str) -> int:
        return client.post(
            "/api/v1/deletion-requests",
            headers=_as(user),
            json={"scope": "document", "document_id": meeting["document_id"]},
        ).status_code

    assert delete("meera") == 403  # can read it, but didn't organise it
    assert delete("ravi") == 404
    assert delete("asha") == 202
    assert client.get("/api/v1/meetings", headers=_as("meera")).json() == []


# --- minutes ----------------------------------------------------------------------------

WITH_ACTIONS = (
    SAFETY_REVIEW
    + "\n3\n00:18:05.000 --> 00:18:12.000\n"
    + "<v Asha>Kavya will update the evacuation map for Building C by Friday.</v>\n"
)


def _draft(client: TestClient, user: str, meeting_id: str) -> Any:
    return client.post(
        f"/api/v1/meetings/{meeting_id}/minutes", headers=_as(user), json={"template": "standard"}
    )


async def test_minutes_are_drafted_by_participants_and_approved_only_by_the_organiser(
    client: TestClient, org: SeedResult, deps: PipelineDeps, queue: NullQueue
) -> None:
    meeting = await _imported(
        client, org, deps, "asha", participants=["meera"], transcript=WITH_ACTIONS
    )
    assert _draft(client, "chitra", meeting["meeting_id"]).status_code == 404  # wasn't there

    draft = _draft(client, "meera", meeting["meeting_id"])
    assert draft.status_code == 201, draft.text
    minutes = draft.json()
    assert minutes["status"] == "draft"
    [decision] = minutes["decisions"]
    assert decision["at"] == "12:40"
    assert "retrained by the end of November" in decision["evidence"]
    [action] = minutes["action_items"]
    assert (action["owner"], action["at"], action["owner_spoke"]) == ("Asha", "18:05", True)

    approve_url = f"/api/v1/minutes/{minutes['id']}/approve"
    assert client.post(approve_url, headers=_as("meera")).status_code == 403  # not organiser
    assert client.post(approve_url, headers=_as("ravi")).status_code == 404
    approved = client.post(approve_url, headers=_as("asha"))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert queue.reindexes == [uuid.UUID(meeting["ingestion_job_id"])]
    assert client.post(approve_url, headers=_as("asha")).status_code == 409  # already decided

    # The reindex job runs: the approved minutes become part of the meeting's memory.
    await run_ingestion(deps, org.organisation_id, uuid.UUID(meeting["ingestion_job_id"]))
    hits = _search(client, "meera", "evacuation map Building C Friday")
    minutes_hits = [h for h in hits if h["heading_path"] == ["Minutes"]]
    assert minutes_hits
    assert minutes_hits[0]["document_id"] == meeting["document_id"]
    for user in ("chitra", "ravi"):
        found = {r["document_id"] for r in _search(client, user, "evacuation map Building C")}
        assert meeting["document_id"] not in found


async def test_the_organiser_can_correct_minutes_while_approving(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    meeting = await _imported(
        client, org, deps, "asha", participants=["meera"], transcript=WITH_ACTIONS
    )
    minutes = _draft(client, "asha", meeting["meeting_id"]).json()
    edited = {
        "summary": "Forklift incident reviewed; retraining and map update agreed.",
        "decisions": [{"text": "Retrain all forklift drivers by 30 November"}],
        "action_items": [{"task": "Update the evacuation map", "owner": "Kavya", "due": "Friday"}],
    }
    approved = client.post(
        f"/api/v1/minutes/{minutes['id']}/approve", headers=_as("asha"), json=edited
    ).json()
    assert approved["edited"] is True
    assert approved["action_items"][0]["owner"] == "Kavya"
    listed = client.get(
        f"/api/v1/meetings/{meeting['meeting_id']}/minutes", headers=_as("meera")
    ).json()
    assert [m["status"] for m in listed] == ["approved"]


async def test_invented_evidence_never_reaches_the_approver(
    org: SeedResult, deps: PipelineDeps, settings: Settings, client: TestClient
) -> None:
    meeting = await _imported(
        client, org, deps, "asha", participants=["meera"], transcript=WITH_ACTIONS
    )
    app = create_app(
        settings.model_copy(
            update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
        ),
        job_queue=NullQueue(),
        language_model=FakeLanguageModel(behaviour=Behaviour.INVENT),
        ask_limiter=AllowAll(),
    )
    with TestClient(app) as inventing:
        minutes = _draft(inventing, "asha", meeting["meeting_id"]).json()
    assert minutes["decisions"] == []
    assert minutes["action_items"] == []
    assert minutes["dropped_items"] == 2


async def test_a_new_transcript_makes_old_drafts_stale(
    client: TestClient, org: SeedResult, deps: PipelineDeps
) -> None:
    meeting = await _imported(
        client,
        org,
        deps,
        "asha",
        participants=["meera"],
        transcript=WITH_ACTIONS,
        external_id="t:1",
    )
    old = _draft(client, "asha", meeting["meeting_id"]).json()
    newer = _import(
        client,
        org,
        "asha",
        participants=["meera"],
        transcript=WITH_ACTIONS.replace("by Friday", "by Monday"),
        external_id="t:1",
    ).json()
    await run_ingestion(deps, org.organisation_id, uuid.UUID(newer["ingestion_job_id"]))
    refused = client.post(f"/api/v1/minutes/{old['id']}/approve", headers=_as("asha"))
    assert refused.status_code == 409
    listed = client.get(
        f"/api/v1/meetings/{meeting['meeting_id']}/minutes", headers=_as("asha")
    ).json()
    assert listed[0]["current"] is False


async def test_deleting_a_meeting_deletes_its_minutes(
    client: TestClient, org: SeedResult, deps: PipelineDeps, owner_engine: AsyncEngine
) -> None:
    meeting = await _imported(
        client, org, deps, "asha", participants=["meera"], transcript=WITH_ACTIONS
    )
    minutes = _draft(client, "asha", meeting["meeting_id"]).json()
    client.post(f"/api/v1/minutes/{minutes['id']}/approve", headers=_as("asha"))
    response = client.post(
        "/api/v1/deletion-requests",
        headers=_as("asha"),
        json={"scope": "document", "document_id": meeting["document_id"]},
    )
    await run_purge(deps, org.organisation_id, uuid.UUID(response.json()["id"]))
    async with owner_engine.connect() as conn:
        left = (
            await conn.execute(
                text("SELECT count(*) FROM meeting_minutes WHERE meeting_id = :m"),
                {"m": meeting["meeting_id"]},
            )
        ).scalar_one()
    assert left == 0
