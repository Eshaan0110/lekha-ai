"""Idempotency-Key parsing, fingerprints and claim handling, without a database."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api.idempotency import (
    REPLAYED_HEADER,
    fingerprint,
    idempotency_key,
    response_for_claim,
)
from db.idempotency import Claim, ClaimOutcome


def _request(value: str | None) -> Request:
    headers = [] if value is None else [(b"idempotency-key", value.encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def test_missing_header_means_no_idempotency() -> None:
    assert idempotency_key(_request(None)) is None


@pytest.mark.parametrize("value", ["3f1c9a2e-8a1b-4c2d-9e3f-1a2b3c4d5e6f", "retry-2026:upload.01"])
def test_valid_keys_are_accepted(value: str) -> None:
    assert idempotency_key(_request(value)) == value


@pytest.mark.parametrize("value", ["short", "has space", "x" * 256, "semi;colon", "ünïcode-key"])
def test_malformed_keys_are_rejected(value: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        idempotency_key(_request(value))
    assert excinfo.value.status_code == 400


def test_fingerprint_separates_parts() -> None:
    assert fingerprint([b"ab", b"c"]) != fingerprint([b"a", b"bc"])
    assert fingerprint([b"same"]) == fingerprint([b"same"])
    assert len(fingerprint([b"x"])) == 32


def test_new_claims_proceed() -> None:
    assert response_for_claim(Claim(ClaimOutcome.NEW)) is None


@pytest.mark.parametrize("outcome", [ClaimOutcome.MISMATCH, ClaimOutcome.IN_PROGRESS])
def test_conflicting_claims_are_409(outcome: ClaimOutcome) -> None:
    with pytest.raises(HTTPException) as excinfo:
        response_for_claim(Claim(outcome))
    assert excinfo.value.status_code == 409


def test_completed_claims_replay_the_stored_response() -> None:
    response = response_for_claim(
        Claim(ClaimOutcome.REPLAY, status=201, body={"document_id": "d1"}, location="/x/d1")
    )
    assert response is not None
    assert response.status_code == 201
    assert response.body == b'{"document_id":"d1"}'
    assert response.headers[REPLAYED_HEADER] == "true"
    assert response.headers["Location"] == "/x/d1"
