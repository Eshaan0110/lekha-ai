from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lekha.audit import AuditEvent
from lekha.core.config import Settings

# Obviously-fake, test-only values that satisfy the minimum-length rule.
TEST_SECRET = "test-secret-not-real-0000"


def _make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "log_format": "json",
        "postgres_password": TEST_SECRET,
        "db_app_password": TEST_SECRET,
        "redis_password": TEST_SECRET,
        "qdrant_api_key": TEST_SECRET,
        "s3_access_key": TEST_SECRET,
        "s3_secret_key": TEST_SECRET,
    }
    values.update(overrides)
    # _env_file=None: unit tests must never read the developer's real .env.
    return Settings(_env_file=None, **values)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    return _make_settings


class InMemoryAuditSink:
    """Collects audit events in memory, for tests that run without a database."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    return InMemoryAuditSink()
