"""Actions end to end: propose → a named approver decides → the worker executes exactly once
against the simulated ticketing system (Phase 5 step 1; CLAUDE.md §6.3)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from apps.worker.actions import ActionDeps, run_action
from apps.worker.pipeline import TransientJobError
from db.actions import SimulatedTicketing
from db.schema import SimulatedTicket
from db.tenancy import tenant_transaction
from lekha.actions import ProposalStatus, TicketInputs
from lekha.connectors import TicketingUnavailableError
from lekha.core.config import AuthMode, Settings
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

TICKET = {
    "title": "Line 3 compressor is leaking",
    "category": "facilities",
    "priority": "P2",
    "description": "Oil under the compressor since the morning shift (synthetic).",
}


class RecordingQueue:
    def __init__(self) -> None:
        self.actions: list[uuid.UUID] = []

    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return None

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        return None

    async def enqueue_action(self, organisation_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
        self.actions.append(proposal_id)

    async def close(self) -> None:
        return None


class DownTicketing:
    async def create_ticket(
        self, organisation_id: uuid.UUID, idempotency_key: str, ticket: TicketInputs
    ) -> str:
        raise TicketingUnavailableError("simulated outage")


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
def deps(app_engine: AsyncEngine) -> ActionDeps:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    return ActionDeps(factory, SimulatedTicketing(factory))


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _propose(
    client: TestClient,
    org: SeedResult,
    user: str,
    *,
    unit: str = "bu-alpha",
    inputs: dict[str, Any] | None = None,
    key: str | None = None,
) -> Any:
    headers = _as(user) | ({"Idempotency-Key": key} if key else {})
    return client.post(
        "/api/v1/actions/propose",
        headers=headers,
        json={
            "business_unit_id": str(org.business_units[unit]),
            "tool": "create_ticket",
            "inputs": inputs if inputs is not None else TICKET,
        },
    )


def _decide(client: TestClient, user: str, proposal_id: str, verb: str, **body: Any) -> Any:
    return client.post(f"/api/v1/actions/{proposal_id}/{verb}", headers=_as(user), json=body)


async def _tickets(owner_engine: AsyncEngine, org: SeedResult) -> int:
    async with owner_engine.connect() as conn:
        count = await conn.scalar(
            select(func.count())
            .select_from(SimulatedTicket)
            .where(SimulatedTicket.organisation_id == org.organisation_id)
        )
    return count or 0


async def test_a_proposal_is_approved_by_a_named_approver_and_executed_once(
    client: TestClient,
    org: SeedResult,
    queue: RecordingQueue,
    deps: ActionDeps,
    owner_engine: AsyncEngine,
) -> None:
    proposed = _propose(client, org, "asha")
    assert proposed.status_code == 201
    proposal = proposed.json()
    assert (proposal["status"], proposal["mine"], proposal["can_decide"]) == (
        "pending_approval",
        True,
        False,
    )
    pid = proposal["id"]
    assert queue.actions == []  # nothing runs before approval
    assert await _tickets(owner_engine, org) == 0

    [pending] = client.get("/api/v1/actions", headers=_as("hari")).json()
    assert (pending["proposed_by"], pending["can_decide"]) == ("Asha (synthetic)", True)

    approved = _decide(client, "hari", pid, "approve", reason="Send maintenance")
    assert approved.status_code == 200
    assert (approved.json()["status"], approved.json()["decided_by"]) == (
        "approved",
        "Hari (synthetic)",
    )
    assert queue.actions == [uuid.UUID(pid)]

    status = await run_action(deps, org.organisation_id, uuid.UUID(pid))
    assert status is ProposalStatus.EXECUTED
    # A duplicate delivery of the job, or a retry, never creates a second ticket.
    assert await run_action(deps, org.organisation_id, uuid.UUID(pid)) is ProposalStatus.EXECUTED
    assert await _tickets(owner_engine, org) == 1

    done = client.get(f"/api/v1/actions/{pid}", headers=_as("asha")).json()
    assert done["status"] == "executed"
    assert done["external_id"].startswith("SIM-")
    assert _decide(client, "hari", pid, "approve").status_code == 409  # decided once


def test_separation_of_duties_and_unit_boundaries(client: TestClient, org: SeedResult) -> None:
    own = _propose(client, org, "hari").json()["id"]  # an approver proposing for himself
    assert _decide(client, "hari", own, "approve").status_code == 403

    pid = _propose(client, org, "asha").json()["id"]
    assert _decide(client, "asha", pid, "approve").status_code == 403  # proposer, not approver
    assert _decide(client, "chitra", pid, "approve").status_code == 404  # curator: not visible
    assert _decide(client, "uma", pid, "approve").status_code == 404  # Beta's approver
    assert client.get(f"/api/v1/actions/{pid}", headers=_as("uma")).status_code == 404
    assert client.get("/api/v1/actions", headers=_as("meera")).json() == []

    assert _propose(client, org, "asha", unit="bu-beta").status_code == 403  # not her unit
    assert _propose(client, org, "nobody").status_code == 403


def test_inputs_are_validated_on_proposal_and_again_when_an_approver_edits(
    client: TestClient, org: SeedResult
) -> None:
    bad = _propose(client, org, "asha", inputs={**TICKET, "assignee": "ravi"})
    assert bad.status_code == 422
    unknown = client.post(
        "/api/v1/actions/propose",
        headers=_as("asha"),
        json={
            "business_unit_id": str(org.business_units["bu-alpha"]),
            "tool": "send_email",
            "inputs": {},
        },
    )
    assert unknown.status_code == 422

    pid = _propose(client, org, "asha").json()["id"]
    invalid_edit = _decide(
        client, "hari", pid, "approve", edited_inputs={**TICKET, "priority": "P0"}
    )
    assert invalid_edit.status_code == 422
    edited = _decide(client, "hari", pid, "approve", edited_inputs={**TICKET, "priority": "P1"})
    assert edited.status_code == 200
    assert edited.json()["inputs"]["priority"] == "P1"


def test_rejection_needs_a_reason_and_is_never_executed(
    client: TestClient, org: SeedResult, queue: RecordingQueue
) -> None:
    pid = _propose(client, org, "asha").json()["id"]
    assert _decide(client, "hari", pid, "reject").status_code == 422
    rejected = _decide(client, "hari", pid, "reject", reason="Already reported as SIM-0001")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert queue.actions == []
    assert client.get(f"/api/v1/actions/{pid}", headers=_as("asha")).json()["reason"] == (
        "Already reported as SIM-0001"
    )


async def test_an_expired_proposal_can_no_longer_be_approved(
    client: TestClient, org: SeedResult, owner_engine: AsyncEngine, queue: RecordingQueue
) -> None:
    pid = _propose(client, org, "asha").json()["id"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE action_proposal SET expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": pid},
        )
    assert client.get(f"/api/v1/actions/{pid}", headers=_as("hari")).json()["status"] == "expired"
    assert _decide(client, "hari", pid, "approve").status_code == 409
    assert client.get(f"/api/v1/actions/{pid}", headers=_as("asha")).json()["status"] == "expired"
    assert queue.actions == []


def test_a_retried_proposal_with_the_same_key_returns_the_first(
    client: TestClient, org: SeedResult
) -> None:
    first = _propose(client, org, "asha", key="log-issue-0001")
    again = _propose(client, org, "asha", key="log-issue-0001")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    different = _propose(
        client, org, "asha", key="log-issue-0001", inputs={**TICKET, "priority": "P4"}
    )
    assert different.status_code == 409
    # Keys are per person: Meera's identical key is a separate proposal.
    assert _propose(client, org, "meera", key="log-issue-0001").status_code == 201
    assert len(client.get("/api/v1/actions", headers=_as("hari")).json()) == 2


async def test_an_outage_is_retried_then_recorded_as_failed(
    client: TestClient, org: SeedResult, app_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    pid = uuid.UUID(_propose(client, org, "asha").json()["id"])
    assert _decide(client, "hari", str(pid), "approve").status_code == 200
    down = ActionDeps(async_sessionmaker(app_engine, expire_on_commit=False), DownTicketing())

    with pytest.raises(TransientJobError):
        await run_action(down, org.organisation_id, pid, attempt=1, final_attempt=False)
    assert client.get(f"/api/v1/actions/{pid}", headers=_as("asha")).json()["status"] == "approved"

    status = await run_action(down, org.organisation_id, pid, attempt=4, final_attempt=True)
    assert status is ProposalStatus.FAILED
    failed = client.get(f"/api/v1/actions/{pid}", headers=_as("asha")).json()
    assert (failed["status"], failed["error_code"]) == ("failed", "ticketing_unavailable")
    assert await _tickets(owner_engine, org) == 0


async def test_the_simulated_ticketing_system_honours_idempotency_keys(
    org: SeedResult, app_engine: AsyncEngine
) -> None:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    system = SimulatedTicketing(factory)
    ticket = TicketInputs.model_validate(TICKET)
    first = await system.create_ticket(org.organisation_id, "key-00000001", ticket)
    assert first == await system.create_ticket(org.organisation_id, "key-00000001", ticket)
    assert first != await system.create_ticket(org.organisation_id, "key-00000002", ticket)
    async with tenant_transaction(factory, uuid.uuid4()) as s:  # another tenant sees nothing
        assert await s.scalar(select(func.count()).select_from(SimulatedTicket)) == 0


def test_actions_are_audited(client: TestClient, org: SeedResult) -> None:
    pid = _propose(client, org, "asha").json()["id"]
    _decide(client, "hari", pid, "approve")
    items = client.get("/api/v1/audit?limit=100", headers=_as("tara")).json()["items"]
    trail = [
        (i["actor"], i["path"].rsplit("/", 1)[-1])
        for i in items
        if i["target_type"] == "action_proposal" and i["target_id"] == pid
    ]
    assert ("Asha (synthetic)", "propose") in trail
    assert ("Hari (synthetic)", "approve") in trail
