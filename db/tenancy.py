"""Tenant-scoped transactions for row-level security.

Every request that reads or writes tenant data runs inside ``tenant_transaction``. It sets
``lekha.tenant_id`` with ``set_config(..., is_local => true)``, so the value lasts only for
this transaction and can never leak to the next user of a pooled connection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker


@asynccontextmanager
async def tenant_transaction(
    session_factory: async_sessionmaker[AsyncSession], organisation_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to one organisation; commits on success, rolls back on error."""
    if not isinstance(organisation_id, uuid.UUID):
        # Fail closed on anything that is not a real UUID (e.g. an unvalidated string).
        raise TypeError("organisation_id must be a uuid.UUID")
    async with session_factory.begin() as session:
        await session.execute(
            text("SELECT set_config('lekha.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(organisation_id)},
        )
        yield session


# Child tables first, so foreign keys are satisfied while deleting. audit_event is absent on
# purpose: it is append-only and outlives the organisations it mentions.
TENANT_TABLES_CHILD_FIRST = (
    "question_request",
    "glossary_name",
    "glossary_entry",
    "answer_feedback",
    "citation",
    "answer_log",
    "query_log",
    "meeting_minutes",
    "meeting_participant",
    "meeting",
    "deletion_request",
    "chunk",
    "ingestion_job",
    "document_acl",
    "document_version",
    "document",
    "document_file",
    "source_system",
    "permission_subject",
    "user_role_grant",
    "role",
    "idempotency_record",
    "app_user",
    "business_unit",
)


async def delete_organisation_rows(conn: AsyncConnection, organisation_id: uuid.UUID) -> None:
    """Remove every row of one organisation. Owner account only (RLS blocks the app role).

    For synthetic dev data and tests; real deletions follow the plan's deletion workflow.
    """
    params = {"o": organisation_id}
    await conn.execute(
        text("UPDATE document SET current_version_id = NULL WHERE organisation_id = :o"), params
    )
    for table in TENANT_TABLES_CHILD_FIRST:
        await conn.execute(text(f"DELETE FROM {table} WHERE organisation_id = :o"), params)  # noqa: S608
    await conn.execute(text("DELETE FROM organisation WHERE id = :o"), params)
