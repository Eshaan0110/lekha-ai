"""Phone calls end to end through the simulator (Phase 5 step 2; typed text stands in for
speech): consent, verification by HR number + one-time code, grounded answers, tickets via
human approval, and hand-over to a person."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import apps.api.voice
from apps.api.ask import AskResponse, CitationOut
from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation, synthetic_contact
from apps.worker.actions import ActionDeps, run_action
from db.actions import SimulatedTicketing
from lekha.auth import AuthContext
from lekha.core.config import AuthMode, Settings
from tests.fixtures.fake_llm import FakeLanguageModel
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

ASHA_HR, ASHA_PHONE = synthetic_contact("asha")
MEERA_HR, MEERA_PHONE = synthetic_contact("meera")


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


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


def _settings(settings: Settings, org: SeedResult) -> Settings:
    return settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )


@pytest.fixture
def client(settings: Settings, org: SeedResult) -> Iterator[TestClient]:
    app = create_app(
        _settings(settings, org), job_queue=NullQueue(), language_model=FakeLanguageModel()
    )
    with TestClient(app) as test_client:
        yield test_client


class Call:
    """A simulated call: what the caller says, and Lekha's replies."""

    def __init__(self, client: TestClient, caller_number: str = "+919999900000") -> None:
        self.client = client
        started = client.post("/api/v1/voice/calls", json={"caller_number": caller_number})
        assert started.status_code == 201
        self.id = started.json()["id"]
        self.replies: list[str] = started.json()["replies"]
        self.state: str = started.json()["state"]

    def say(self, words: str) -> list[str]:
        response = self.client.post(f"/api/v1/voice/calls/{self.id}/say", json={"text": words})
        assert response.status_code == 200, response.text
        self.state = response.json()["state"]
        self.replies = response.json()["replies"]
        return self.replies

    def say_raw(self, words: str) -> Any:
        return self.client.post(f"/api/v1/voice/calls/{self.id}/say", json={"text": words})

    def transcript(self) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = self.client.get(f"/api/v1/voice/calls/{self.id}").json()[
            "turns"
        ]
        return turns


def _outbox(client: TestClient, phone: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = client.get(
        "/api/v1/voice/outbox", params={"phone": phone}
    ).json()
    return messages


def _latest_code(client: TestClient, phone: str) -> str:
    codes = [m for m in _outbox(client, phone) if m["kind"] == "verification_code"]
    match = re.search(r"\b(\d{6})\b", codes[0]["body"])
    assert match
    return match.group(1)


def _verified_call(client: TestClient, hr: str = ASHA_HR, phone: str = ASHA_PHONE) -> Call:
    call = Call(client)
    call.say(hr)
    call.say(_latest_code(client, phone))
    assert call.state == "ready"
    return call


def _escalations(client: TestClient) -> dict[str, str]:
    items = client.get("/api/v1/voice/escalations").json()
    return {e["call_id"]: e["reason"] for e in items}


def test_a_call_opens_with_the_consent_notice_in_all_three_languages(client: TestClient) -> None:
    call = Call(client)
    assert call.state == "awaiting_id"
    assert len(call.replies) == 3
    assert "ಲೇಖಾ" in call.replies[0]
    assert "लेखा" in call.replies[1]
    assert "Lekha" in call.replies[2]
    assert "employee number" in call.replies[2]


def test_verification_needs_the_code_sent_to_the_phone_registered_with_hr(
    client: TestClient,
) -> None:
    # Calling FROM Asha's registered number proves nothing: caller ID is never trusted.
    call = Call(client, caller_number=ASHA_PHONE)
    # A question before verification is not answered; Lekha asks for the number again.
    assert call.say("How many days of leave do I get?") == [
        "Sorry, I didn't get that. Please say the five digits of your employee number."
    ]
    assert call.state == "awaiting_id"
    call.say(f"my number is {ASHA_HR[3:]}")
    assert call.state == "awaiting_code"
    code = _latest_code(client, ASHA_PHONE)
    assert call.say(code)[0].startswith("Thank you, you are verified")
    assert call.state == "ready"
    # The code is in the (simulated) SMS, never in the call transcript.
    assert all(code not in t["text"] for t in call.transcript())


def test_unknown_and_inactive_numbers_get_the_same_reply_and_no_code(client: TestClient) -> None:
    known = Call(client).say(ASHA_HR)
    unknown = Call(client).say("MG-99999")
    inactive_hr, inactive_phone = synthetic_contact("inactive")
    inactive = Call(client)
    assert inactive.say(inactive_hr) == known == unknown  # nothing reveals which are real
    assert _outbox(client, inactive_phone) == []
    for guess in ("000000", "111111", "222222"):
        inactive.say(guess)
    assert inactive.state == "escalated"


def test_three_wrong_codes_hand_the_call_to_a_person(client: TestClient) -> None:
    call = Call(client)
    call.say(MEERA_HR)  # someone claiming to be Meera: the code goes to Meera's phone
    right = _latest_code(client, MEERA_PHONE)
    wrong = [c for c in ("123456", "654321", "000001", "999999") if c != right][:3]
    assert call.say(wrong[0]) == ["That code is not right. Please try again."]
    call.say(wrong[1])
    call.say(wrong[2])
    assert call.state == "escalated"
    assert _escalations(client)[call.id] == "verification_failed"
    assert call.say_raw(right).status_code == 409  # the right code is too late now


def test_a_code_works_once_and_only_on_its_own_call(client: TestClient) -> None:
    _verified_call(client)
    used = _latest_code(client, ASHA_PHONE)
    other = Call(client)
    other.say(ASHA_HR)
    assert other.say(used) == ["That code is not right. Please try again."]
    assert other.state == "awaiting_code"
    other.say(_latest_code(client, ASHA_PHONE))
    assert other.state == "ready"


async def test_an_expired_code_is_refused(client: TestClient, owner_engine: AsyncEngine) -> None:
    call = Call(client)
    call.say(ASHA_HR)
    code = _latest_code(client, ASHA_PHONE)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE verification_code SET expires_at = now() - interval '1 second' "
                "WHERE call_id = :c"
            ),
            {"c": call.id},
        )
    assert call.say(code) == ["That code is not right. Please try again."]
    assert call.state == "awaiting_code"


def test_codes_per_employee_are_limited_so_they_cannot_be_flooded_or_farmed(
    client: TestClient,
) -> None:
    for _ in range(3):
        Call(client).say(MEERA_HR)
    assert len(_outbox(client, MEERA_PHONE)) == 3
    fourth = Call(client)
    assert fourth.say(MEERA_HR) == [
        "If that number is registered, a code has been sent to the phone registered with HR. "
        "Please say the six-digit code."
    ]
    assert len(_outbox(client, MEERA_PHONE)) == 3  # no more codes this hour, same reply
    fourth.say(_latest_code(client, MEERA_PHONE))  # an earlier call's code is useless here
    assert fourth.state == "awaiting_code"


def test_language_comes_from_the_first_sentence(client: TestClient) -> None:
    call = Call(client)
    replies = call.say(f"ನನ್ನ ಸಂಖ್ಯೆ ೧೦೦೦{ASHA_HR[-1]}")
    assert call.state == "awaiting_code"
    assert "ಕೋಡ್" in replies[0]  # answered in Kannada
    hindi = Call(client)
    assert "कोड" in hindi.say(f"मेरा नंबर {ASHA_HR[3:]} है")[0]


def test_a_grounded_answer_is_spoken_briefly_and_sent_in_full_by_sms(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str]] = []

    async def grounded(request: Any, ctx: AuthContext, question: str) -> AskResponse:
        asked.append((ctx.display_name, question))
        return AskResponse(
            query_id=uuid.uuid4(),
            answer="You get 18 days of paid leave a year [1]. Unused days carry over [1].",
            abstained=False,
            abstention_reason=None,
            citations=[
                CitationOut(
                    number=1,
                    document_id=uuid.uuid4(),
                    version_id=uuid.uuid4(),
                    version_number=1,
                    title="Alpha leave policy (synthetic)",
                    heading_path=[],
                    chunk_id=uuid.uuid4(),
                    span="18 days of paid leave",
                )
            ],
            language="en",
            model="fake",
        )

    monkeypatch.setattr(apps.api.voice, "answer_question", grounded)
    call = _verified_call(client)
    replies = call.say("How many days of leave do I get?")
    # Answered as the verified employee, with that employee's own permissions.
    assert asked == [("Asha (synthetic)", "How many days of leave do I get?")]
    assert replies == [
        "You get 18 days of paid leave a year. Unused days carry over.",
        "The full answer and its sources have been sent by SMS.",
    ]
    [sms] = [m for m in _outbox(client, ASHA_PHONE) if m["kind"] == "answer"]
    assert "[1] Alpha leave policy (synthetic)" in sms["body"]
    assert call.state == "ready"


def test_an_unanswerable_question_goes_to_a_person(client: TestClient) -> None:
    call = _verified_call(client)
    replies = call.say("Is there a gym in the office?")  # nothing indexed: no sources
    assert call.state == "escalated"
    assert replies[-1] == "I am connecting you to a person. Please hold."
    assert _escalations(client)[call.id] == "out_of_scope"


def test_a_system_failure_goes_to_a_person(settings: Settings, org: SeedResult) -> None:
    app = create_app(_settings(settings, org), job_queue=NullQueue())  # no answer model
    with TestClient(app) as client:
        call = _verified_call(client)
        call.say("How many days of leave do I get?")
        assert call.state == "escalated"
        assert _escalations(client)[call.id] == "system_failure"


@pytest.mark.parametrize(
    ("words", "reason"),
    [("Can I talk to a person please", "caller_request"), ("This is useless!", "upset")],
)
def test_callers_can_always_reach_a_person(client: TestClient, words: str, reason: str) -> None:
    before = Call(client)  # even before verification
    before.say(words)
    assert _escalations(client)[before.id] == reason
    after = _verified_call(client)
    after.say(words)
    assert _escalations(client)[after.id] == reason


async def test_a_ticket_logged_by_phone_needs_confirmation_and_a_named_approver(
    client: TestClient, org: SeedResult, app_engine: AsyncEngine
) -> None:
    call = _verified_call(client)
    readback = call.say("Log a complaint about the Line 3 compressor leaking oil")[0]
    assert readback.startswith("I will log this: The Line 3 compressor leaking oil.")
    assert call.state == "confirming_ticket"
    assert client.get("/api/v1/actions", headers={"X-Dev-User": "dev|hari"}).json() == []

    assert call.say("yes")[0].startswith("Your complaint has been sent for approval")
    [proposal] = client.get("/api/v1/actions", headers={"X-Dev-User": "dev|hari"}).json()
    assert (proposal["proposed_by"], proposal["status"]) == ("Asha (synthetic)", "pending_approval")
    assert "waiting for approval" in call.say("What is my ticket status?")[0]

    approve = client.post(
        f"/api/v1/actions/{proposal['id']}/approve", headers={"X-Dev-User": "dev|hari"}, json={}
    )
    assert approve.status_code == 200
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    await run_action(
        ActionDeps(factory, SimulatedTicketing(factory)),
        org.organisation_id,
        uuid.UUID(proposal["id"]),
    )
    [sms] = [m for m in _outbox(client, ASHA_PHONE) if m["kind"] == "ticket"]
    reference = re.search(r"SIM-[0-9A-F]{8}", sms["body"])
    assert reference
    assert reference.group(0) in call.say("ticket status")[0]


def test_a_ticket_can_be_cancelled_at_the_read_back(client: TestClient) -> None:
    call = _verified_call(client)
    call.say("I want to report that the canteen fan is broken")
    assert call.say("no") == ["All right, I have not logged it."]
    assert call.state == "ready"
    assert client.get("/api/v1/actions", headers={"X-Dev-User": "dev|hari"}).json() == []


def test_a_caller_with_no_role_cannot_log_a_ticket(client: TestClient) -> None:
    nobody_hr, nobody_phone = synthetic_contact("nobody")
    call = _verified_call(client, nobody_hr, nobody_phone)
    call.say("log a complaint: the lift is stuck")
    assert call.say("yes") == ["Sorry, I can't log a complaint for you."]


async def test_an_idle_call_times_out(client: TestClient, owner_engine: AsyncEngine) -> None:
    call = Call(client)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE voice_call SET last_activity_at = now() - interval '6 minutes' "
                "WHERE id = :c"
            ),
            {"c": call.id},
        )
    assert call.say_raw(ASHA_HR).status_code == 409
    assert client.get(f"/api/v1/voice/calls/{call.id}").json()["state"] == "ended"


def test_goodbye_and_hang_up_end_the_call(client: TestClient) -> None:
    call = _verified_call(client)
    assert call.say("bye") == ["Thank you. Goodbye."]
    assert call.state == "ended"
    other = Call(client)
    assert client.post(f"/api/v1/voice/calls/{other.id}/hangup").json()["state"] == "ended"
    assert client.post(f"/api/v1/voice/calls/{other.id}/hangup").status_code == 409


def test_the_simulator_exists_only_with_development_sign_in(
    settings: Settings, org: SeedResult
) -> None:
    production_like = settings.model_copy(update={"auth_mode": AuthMode.OIDC})
    with TestClient(create_app(production_like, job_queue=NullQueue())) as client:
        assert client.post("/api/v1/voice/calls", json={"caller_number": "+911"}).status_code == 404
        assert client.get("/api/v1/voice/outbox").status_code == 404
