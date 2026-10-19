"""Integration tests against the real local Docker Compose stack.

Run with ``./dev.ps1 up`` followed by ``./dev.ps1 test-integration``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.main import create_app
from db.engine import create_engine
from lekha.core.config import Settings
from tests.integration.support import run_alembic

pytestmark = pytest.mark.integration


def test_all_dependencies_ready(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": "ok", "redis": "ok", "qdrant": "ok", "object_storage": "ok"},
    }


def test_wrong_credentials_make_readiness_fail_closed(settings: Settings) -> None:
    wrong = settings.model_copy(
        update={
            "db_app_password": settings.redis_password,  # a real but wrong secret
            "redis_password": settings.qdrant_api_key,
            "qdrant_api_key": settings.postgres_password,
        }
    )
    with TestClient(create_app(wrong)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["postgres"] == "unavailable"
    assert checks["redis"] == "unavailable"
    assert checks["qdrant"] == "unavailable"


async def test_engine_applies_statement_timeout(settings: Settings) -> None:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            timeout = (await conn.execute(text("SHOW statement_timeout"))).scalar_one()
    finally:
        await engine.dispose()
    assert timeout == "15s"


def test_migrations_upgrade_downgrade_and_match_models() -> None:
    # Runs against the throwaway test database; ends at head for the other tests.
    for args in (("downgrade", "base"), ("upgrade", "head"), ("check",)):
        result = run_alembic(*args)
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"
