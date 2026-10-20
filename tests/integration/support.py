"""Helpers shared by integration tests (kept out of conftest.py, which pytest owns)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine

from db.tenancy import TENANT_TABLES_CHILD_FIRST, delete_organisation_rows

TEST_DATABASE = "lekha_test"
TEST_BUCKET = "lekha-test"
TEST_COLLECTION = "lekha_test_chunks"

__all__ = [
    "TENANT_TABLES_CHILD_FIRST",
    "TEST_BUCKET",
    "TEST_COLLECTION",
    "TEST_DATABASE",
    "delete_tenant",
    "run_alembic",
]


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an Alembic command against the throwaway test database."""
    env = {**os.environ, "LEKHA_POSTGRES_DB": TEST_DATABASE}
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
    )


async def delete_tenant(engine: AsyncEngine, org: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await delete_organisation_rows(conn, org)
