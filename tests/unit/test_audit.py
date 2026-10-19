"""Audit middleware behaviour that needs no database."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from lekha.audit import AuditEvent, AuditSink, Outcome, outcome_for_status
from lekha.core.config import Settings
from tests.conftest import InMemoryAuditSink


class FailingAuditSink:
    async def record(self, event: AuditEvent) -> None:
        raise ConnectionError("audit store unavailable")


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, Outcome.SUCCESS),
        (304, Outcome.SUCCESS),
        (400, Outcome.REJECTED),
        (401, Outcome.DENIED),
        (403, Outcome.DENIED),
        (404, Outcome.DENIED),
        (422, Outcome.REJECTED),
        (500, Outcome.ERROR),
        (503, Outcome.ERROR),
    ],
)
def test_outcome_mapping(status: int, outcome: Outcome) -> None:
    assert outcome_for_status(status) is outcome


def _client(settings: Settings, sink: AuditSink) -> TestClient:
    return TestClient(
        create_app(settings, readiness_checks=[], audit_sink=sink),
        raise_server_exceptions=False,
    )


def test_every_api_request_is_audited_with_correlation(
    make_settings: Callable[..., Settings], audit_sink: InMemoryAuditSink
) -> None:
    with _client(make_settings(), audit_sink) as client:
        client.get(
            "/api/v1/documents/7d3c0e9e-55b0-4a7e-9e57-1f0c8b9c2a11?token=secret-value",
            headers={"X-Request-ID": "rid-audit-1"},
        )
    [event] = audit_sink.events
    assert event.request_id == "rid-audit-1"
    assert event.action == "GET /api/v1/documents/{document_id}"
    assert event.path == "/api/v1/documents/7d3c0e9e-55b0-4a7e-9e57-1f0c8b9c2a11"
    assert (event.target_type, str(event.target_id)) == (
        "document",
        "7d3c0e9e-55b0-4a7e-9e57-1f0c8b9c2a11",
    )
    assert (event.status_code, event.outcome) == (401, Outcome.DENIED)
    assert (event.organisation_id, event.actor_user_id) == (None, None)  # never signed in
    assert "secret-value" not in repr(event)  # query strings are never recorded


def test_unknown_api_routes_are_audited_too(
    make_settings: Callable[..., Settings], audit_sink: InMemoryAuditSink
) -> None:
    with _client(make_settings(), audit_sink) as client:
        assert client.get("/api/v1/does-not-exist").status_code == 404
    [event] = audit_sink.events
    assert event.action == "GET <unmatched>"
    assert event.outcome is Outcome.DENIED


def test_health_and_docs_are_not_audited(
    make_settings: Callable[..., Settings], audit_sink: InMemoryAuditSink
) -> None:
    with _client(make_settings(), audit_sink) as client:
        client.get("/health/live")
        client.get("/openapi.json")
    assert audit_sink.events == []


def test_response_is_withheld_when_the_audit_write_fails(
    make_settings: Callable[..., Settings],
) -> None:
    with _client(make_settings(), FailingAuditSink()) as client:
        response = client.get("/api/v1/me", headers={"X-Request-ID": "rid-audit-fail"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "request_id": "rid-audit-fail",
        }
    }
    assert response.headers["X-Request-ID"] == "rid-audit-fail"
