"""Integration fixtures: a throwaway ``lekha_test`` database on the local stack.

Integration tests never touch the developer's ``lekha`` database. The test database is
recreated once per test session and migrated to head as the owner account.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from db.engine import create_engine, database_url
from lekha.core.config import Settings
from lekha.memory.s3_store import S3ObjectStore
from tests.integration.support import TEST_BUCKET, TEST_COLLECTION, TEST_DATABASE, run_alembic


async def _recreate_test_database(settings: Settings) -> None:
    admin = create_async_engine(
        database_url(settings.model_copy(update={"postgres_db": "postgres"}), owner=True),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)"))
            await conn.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def settings() -> Settings:
    dev = Settings()  # reads the developer's .env
    asyncio.run(_recreate_test_database(dev))
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    test_settings = dev.model_copy(
        update={
            "postgres_db": TEST_DATABASE,
            "s3_bucket": TEST_BUCKET,
            "qdrant_collection": TEST_COLLECTION,
            # Deterministic vectors whatever the developer's .env chooses.
            "embedder": "fake",
            # No real language model unless a test injects one (tests never call Ollama).
            "llm": "none",
        }
    )
    asyncio.run(S3ObjectStore.from_settings(test_settings).ensure_bucket())
    # A fresh vector collection per test session (created on first use by the pipeline).
    httpx.delete(
        f"{str(dev.qdrant_url).rstrip('/')}/collections/{TEST_COLLECTION}",
        headers={"api-key": dev.qdrant_api_key.get_secret_value()},
        timeout=10,
    )
    return test_settings


@pytest.fixture
async def owner_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Owner account: bypasses row-level security; used to arrange test data."""
    engine = create_async_engine(
        database_url(settings, owner=True), poolclass=NullPool, hide_parameters=True
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """The least-privilege account the API uses."""
    engine = create_engine(settings)
    yield engine
    await engine.dispose()
