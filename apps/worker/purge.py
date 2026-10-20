"""Purge a deleted document's derived data (plan: deletion worker walks document → version →
chunk → vector → file). The document is already hidden by the API's soft delete; this removes
what is left. Every step is idempotent, so a retry after an outage simply runs again.

Local development deletes immediately. The plan's retention window (hard delete on a nightly
job after the retention period) needs the retention policy, which Phase 0 has not set yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from apps.worker.pipeline import PipelineDeps, TransientJobError
from db.deletion import delete_document_chunks, load_purge_target, resolve_request
from db.schema import DeletionStatus
from db.tenancy import tenant_transaction
from lekha.memory import ObjectStoreError
from lekha.retrieval import VectorIndexError

logger = structlog.get_logger("lekha.deletion")


async def run_purge(
    deps: PipelineDeps,
    organisation_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    final_attempt: bool = True,
) -> DeletionStatus | None:
    async with tenant_transaction(deps.session_factory, organisation_id) as s:
        target = await load_purge_target(s, request_id)
    if target is None:
        logger.warning("deletion_request_not_found")
        return None
    if target.status is DeletionStatus.COMPLETED:
        return target.status
    structlog.contextvars.bind_contextvars(deletion_request_id=str(request_id))
    try:
        await deps.index.ensure_collection()
        await deps.index.delete_document(target.document_id)
        async with tenant_transaction(deps.session_factory, organisation_id) as s:
            chunks = await delete_document_chunks(s, target.document_id)
        for key in target.storage_keys:
            await deps.store.delete(key)
    except (ObjectStoreError, VectorIndexError) as exc:
        code = "storage_unavailable" if isinstance(exc, ObjectStoreError) else "index_unavailable"
        if final_attempt:
            async with tenant_transaction(deps.session_factory, organisation_id) as s:
                await resolve_request(
                    s,
                    request_id,
                    status=DeletionStatus.FAILED,
                    now=datetime.now(UTC),
                    error_code=code,
                )
        logger.warning("deletion_purge_failed", error_code=code, final=final_attempt)
        raise TransientJobError(code) from None

    async with tenant_transaction(deps.session_factory, organisation_id) as s:
        await resolve_request(
            s,
            request_id,
            status=DeletionStatus.COMPLETED,
            now=datetime.now(UTC),
            chunks_removed=chunks,
            files_removed=len(target.storage_keys),
        )
    logger.info("deletion_completed", chunks_removed=chunks, files_removed=len(target.storage_keys))
    return DeletionStatus.COMPLETED
