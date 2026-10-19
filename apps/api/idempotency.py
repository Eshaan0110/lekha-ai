"""The ``Idempotency-Key`` header for write endpoints (plan API convention).

A client that may retry a write sends a unique key (a UUID is ideal). The first request with
that key does the work; a retry of the *same* request replays the stored response with
``Idempotent-Replayed: true``. Reusing a key for a different request, or while the first is
still running, is a ``409 conflict``. Keys are per user and expire after 24 hours.
Requests without the header behave exactly as before.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from db.idempotency import Claim, ClaimOutcome

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,255}$")


def idempotency_key(request: Request) -> str | None:
    """The validated header value, None if absent, or 400 if malformed."""
    value = request.headers.get(IDEMPOTENCY_HEADER)
    if value is None:
        return None
    if not _KEY.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail=(
                "Idempotency-Key must be 8-255 characters: letters, digits, '.', '_', ':' or '-'."
            ),
        )
    return value


def fingerprint(parts: Iterable[bytes]) -> bytes:
    """SHA-256 over length-prefixed parts, so ("ab", "c") and ("a", "bc") differ."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.digest()


def response_for_claim(claim: Claim) -> JSONResponse | None:
    """The response to return now, or None if the caller should do the work."""
    if claim.outcome is ClaimOutcome.NEW:
        return None
    if claim.outcome is ClaimOutcome.MISMATCH:
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used for a different request.",
        )
    if claim.outcome is ClaimOutcome.IN_PROGRESS:
        raise HTTPException(
            status_code=409, detail="A request with this Idempotency-Key is still in progress."
        )
    if claim.status is None or claim.body is None:  # prevented by a database constraint
        raise RuntimeError("completed idempotency record has no stored response")
    headers = {REPLAYED_HEADER: "true"}
    if claim.location:
        headers["Location"] = claim.location
    return JSONResponse(status_code=claim.status, content=claim.body, headers=headers)
