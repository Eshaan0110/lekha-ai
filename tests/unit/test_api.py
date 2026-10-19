"""API behaviour that needs no running infrastructure: request IDs, errors, health."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from apps.api.main import create_app
from apps.api.middleware import REQUEST_ID_HEADER, resolve_request_id
from lekha.core.config import Settings
from lekha.core.health import ReadinessCheck

LEAKED_SECRET = "postgres://admin:hunter2@10.0.0.5"


async def _ok() -> None:
    return None


async def _fails() -> None:
    raise ConnectionError(f"could not connect to {LEAKED_SECRET}")


async def _hangs() -> None:
    await asyncio.sleep(60)


class _Payload(BaseModel):
    name: str = Field(min_length=3)


def _add_test_routes(app: FastAPI) -> None:
    @app.post("/test/validate")
    async def validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    @app.get("/test/boom")
    async def boom() -> None:
        raise RuntimeError(f"internal detail {LEAKED_SECRET}")


@pytest.fixture
def client_factory(
    make_settings: Callable[..., Settings],
) -> Iterator[Callable[[list[ReadinessCheck]], TestClient]]:
    clients: list[TestClient] = []

    def build(checks: list[ReadinessCheck]) -> TestClient:
        app = create_app(make_settings(readiness_timeout_seconds=0.2), readiness_checks=checks)
        _add_test_routes(app)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[[list[ReadinessCheck]], TestClient]) -> TestClient:
    return client_factory([ReadinessCheck("postgres", _ok)])


# --- request IDs ---------------------------------------------------------------


def test_valid_incoming_request_id_is_kept() -> None:
    assert resolve_request_id("abc-123_X.y") == "abc-123_X.y"


@pytest.mark.parametrize(
    "incoming", [None, "", "has space", "line\nbreak", "x" * 65, '{"inject": true}']
)
def test_unsafe_or_missing_request_id_is_replaced(incoming: str | None) -> None:
    generated = resolve_request_id(incoming)
    assert generated != incoming
    assert len(generated) == 32


def test_response_echoes_caller_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "trace-42"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-42"


def test_response_gets_generated_request_id(client: TestClient) -> None:
    first = client.get("/health/live").headers[REQUEST_ID_HEADER]
    second = client.get("/health/live").headers[REQUEST_ID_HEADER]
    assert first != second


# --- error envelope ------------------------------------------------------------


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get("/nope", headers={REQUEST_ID_HEADER: "rid-404"})
    assert response.status_code == 404
    # The plan's envelope: code, message, detail. The request ID travels in the header.
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found", "detail": {}}}
    assert response.headers[REQUEST_ID_HEADER] == "rid-404"


def test_validation_error_reports_fields_without_echoing_input(client: TestClient) -> None:
    response = client.post("/test/validate", json={"name": "zz"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["detail"]["fields"][0]["location"] == ["body", "name"]
    assert "zz" not in response.text


def test_unhandled_exception_returns_generic_500(client: TestClient) -> None:
    response = client.get("/test/boom", headers={REQUEST_ID_HEADER: "rid-500"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "detail": {},
        }
    }
    assert response.headers[REQUEST_ID_HEADER] == "rid-500"
    assert "hunter2" not in response.text


# --- health --------------------------------------------------------------------


def test_liveness_needs_no_dependencies(
    client_factory: Callable[[list[ReadinessCheck]], TestClient],
) -> None:
    client = client_factory([ReadinessCheck("postgres", _fails)])
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_all_checks_pass(
    client_factory: Callable[[list[ReadinessCheck]], TestClient],
) -> None:
    client = client_factory([ReadinessCheck("postgres", _ok), ReadinessCheck("redis", _ok)])
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"postgres": "ok", "redis": "ok"}}


def test_not_ready_when_a_check_fails_and_error_is_not_leaked(
    client_factory: Callable[[list[ReadinessCheck]], TestClient],
) -> None:
    client = client_factory([ReadinessCheck("postgres", _fails), ReadinessCheck("redis", _ok)])
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"postgres": "unavailable", "redis": "ok"},
    }
    assert "hunter2" not in response.text


def test_hanging_dependency_times_out_instead_of_blocking(
    client_factory: Callable[[list[ReadinessCheck]], TestClient],
) -> None:
    client = client_factory([ReadinessCheck("qdrant", _hangs)])
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"qdrant": "unavailable"}


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_interactive_docs_are_disabled_in_production(
    make_settings: Callable[..., Settings], path: str
) -> None:
    app = create_app(make_settings(environment="production"), readiness_checks=[])
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


# --- logging -------------------------------------------------------------------


def test_logs_are_json_correlated_and_free_of_secrets(
    client_factory: Callable[[list[ReadinessCheck]], TestClient],
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = client_factory([ReadinessCheck("postgres", _fails)])
    capsys.readouterr()  # discard startup logs
    client.get("/health/ready?token=abc123secret", headers={REQUEST_ID_HEADER: "rid-log"})
    client.get("/test/boom", headers={REQUEST_ID_HEADER: "rid-log2"})
    output = capsys.readouterr().out

    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    access = [e for e in events if e["event"] == "http_request" and e["request_id"] == "rid-log"]
    assert access == [
        {
            **access[0],
            "status": 503,
            "path": "/health/ready",
            "method": "GET",
            "request_id": "rid-log",
        }
    ]
    failed_check = next(e for e in events if e["event"] == "readiness_check_failed")
    assert failed_check["request_id"] == "rid-log"
    assert failed_check["error"] == "ConnectionError"

    crash = next(e for e in events if e["event"] == "unhandled_exception")
    assert crash["request_id"] == "rid-log2"
    assert "exception" in crash  # stack trace is kept server-side for debugging

    assert "abc123secret" not in output  # query strings are never logged
    # Exception text appears only in the server-side stack trace, never in other fields.
    for event in events:
        without_trace = {k: v for k, v in event.items() if k != "exception"}
        assert "hunter2" not in json.dumps(without_trace)
