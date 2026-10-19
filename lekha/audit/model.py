"""Audit events: the append-only record of who did what, when, and with what result.

Pure Python (no framework or database code). An event never carries document contents,
tokens, names, or query strings: only identifiers, the action, and the outcome.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

MAX_PATH_LENGTH = 512


class Outcome(StrEnum):
    SUCCESS = "success"  # 1xx-3xx
    DENIED = "denied"  # 401, 403, 404: not signed in, not allowed, or not visible
    REJECTED = "rejected"  # other 4xx: the request itself was invalid
    ERROR = "error"  # 5xx: the server failed


_DENIED_STATUSES = frozenset({401, 403, 404})


def outcome_for_status(status_code: int) -> Outcome:
    if status_code >= 500:
        return Outcome.ERROR
    if status_code in _DENIED_STATUSES:
        return Outcome.DENIED
    if status_code >= 400:
        return Outcome.REJECTED
    return Outcome.SUCCESS


@dataclass(frozen=True, slots=True)
class AuditEvent:
    request_id: str
    action: str  # e.g. "GET /api/v1/documents/{document_id}" (route template, not raw path)
    method: str
    path: str  # raw path without the query string, truncated to MAX_PATH_LENGTH
    status_code: int
    outcome: Outcome
    organisation_id: uuid.UUID | None = None  # None when the caller never signed in
    actor_user_id: uuid.UUID | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None:
        """Persist the event durably, or raise. Callers must treat a failure as fatal."""
        ...
