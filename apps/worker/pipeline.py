"""The ingestion pipeline for one job: extract → chunk → embed → index → publish (plan
stages 4-13). A new version becomes current only after its chunks are indexed.

Runs inside the ARQ worker, and is called directly by tests. Every write is scoped to the
job's organisation. Deterministic failures (an unreadable or empty file) mark the job
``failed`` with a reason code and are not retried. Temporary failures (storage or index
unavailable) put it back to ``queued`` with the reason and re-raise, so the queue retries
with backoff; only the last attempt marks it ``failed``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.ingestion import (
    VersionToIngest,
    load_version_to_ingest,
    publish_version,
    replace_chunks,
    update_job,
)
from db.schema import IngestionJob, IngestionStatus
from db.tenancy import tenant_transaction
from lekha.ingestion import (
    Chunk,
    ExtractionError,
    TranscriptError,
    chunk_sections,
    chunk_transcript,
    extract_sections,
    from_canonical_json,
)
from lekha.memory import FileKind, ObjectStoreError
from lekha.memory.s3_store import S3ObjectStore
from lekha.retrieval import IndexedPoint, QdrantIndex, VectorIndexError
from model_layer import Embedder

logger = structlog.get_logger("lekha.ingestion")

EMBED_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    session_factory: async_sessionmaker[AsyncSession]
    store: S3ObjectStore
    index: QdrantIndex
    embedder: Embedder


class TransientJobError(RuntimeError):
    """A dependency was unavailable; the queue should retry this job (ingestion or purge)."""


async def run_ingestion(
    deps: PipelineDeps,
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    final_attempt: bool = True,
) -> IngestionStatus | None:
    now = datetime.now(UTC)
    async with tenant_transaction(deps.session_factory, organisation_id) as s:
        target = await load_version_to_ingest(s, job_id, now=now)
        if target is None:
            logger.warning("ingestion_job_not_found")
            return None
        await update_job(
            s,
            job_id,
            status=IngestionStatus.RUNNING,
            stage="extract",
            attempts=IngestionJob.attempts + 1,
            error_code=None,
            started_at=now,
        )
    structlog.contextvars.bind_contextvars(ingestion_job_id=str(job_id))
    if target.deleted:
        return await _finish(deps, target, IngestionStatus.FAILED, error_code="document_deleted")
    try:
        chunks = await _extract_and_chunk(deps, target)
        if not chunks:
            return await _finish(deps, target, IngestionStatus.FAILED, error_code="no_text")
        await _set_stage(deps, target, "embed")
        vectors = await _embed(deps.embedder, chunks)
        await _set_stage(deps, target, "index")
        await _index(deps, target, chunks, vectors)
    except ExtractionError as exc:
        return await _finish(deps, target, IngestionStatus.FAILED, error_code=exc.code)
    except (ObjectStoreError, VectorIndexError) as exc:
        code = "storage_unavailable" if isinstance(exc, ObjectStoreError) else "index_unavailable"
        status = IngestionStatus.FAILED if final_attempt else IngestionStatus.QUEUED
        await _finish(deps, target, status, error_code=code)
        raise TransientJobError(code) from None
    return await _finish(deps, target, IngestionStatus.SUCCEEDED, chunk_count=len(chunks))


async def _extract_and_chunk(deps: PipelineDeps, target: VersionToIngest) -> list[Chunk]:
    data, _ = await deps.store.get(target.storage_key)
    if target.kind is FileKind.TRANSCRIPT:
        try:
            segments = from_canonical_json(data)
        except TranscriptError as exc:
            raise ExtractionError(exc.code) from None
        await _set_stage(deps, target, "chunk")
        return chunk_transcript(segments)
    sections = extract_sections(target.kind, data)
    await _set_stage(deps, target, "chunk")
    return chunk_sections(sections)


async def _embed(embedder: Embedder, chunks: list[Chunk]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = [c.text for c in chunks[start : start + EMBED_BATCH_SIZE]]
        vectors.extend(await asyncio.to_thread(embedder.embed, batch))
    return vectors


async def _index(
    deps: PipelineDeps,
    target: VersionToIngest,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> None:
    point_ids = [uuid.uuid4() for _ in chunks]
    await deps.index.ensure_collection()
    await deps.index.delete_version(target.version_id)  # a retry replaces earlier points
    await deps.index.upsert(
        [
            IndexedPoint(
                id=point_id, vector=vector, payload=_payload(target, chunk, point_id, deps)
            )
            for chunk, vector, point_id in zip(chunks, vectors, point_ids, strict=True)
        ]
    )
    async with tenant_transaction(deps.session_factory, target.organisation_id) as s:
        await replace_chunks(s, target, chunks, point_ids, embedding_model=deps.embedder.model_name)
        published = await publish_version(s, target, now=datetime.now(UTC))
    if published.current_version_id is None:
        # Deleted while processing: its points must not linger (the purge may have run first).
        await deps.index.delete_document(target.document_id)
    else:
        await deps.index.mark_current_version(target.document_id, published.current_version_id)


def _payload(
    target: VersionToIngest, chunk: Chunk, point_id: uuid.UUID, deps: PipelineDeps
) -> dict[str, object]:
    """Permissions travel with every point (plan: ADR-006 'Permission tags in Qdrant')."""
    return {
        "chunk_id": str(point_id),
        "tenant_id": str(target.organisation_id),
        "business_unit_id": str(target.business_unit_id),
        "document_id": str(target.document_id),
        "document_version_id": str(target.version_id),
        "classification": target.classification.value,
        "classification_rank": target.classification.rank,
        "allowed_subjects": list(target.allowed_subjects),
        "language": chunk.language.value,
        "is_current_version": target.is_current,
        "embedding_model": deps.embedder.model_name,
    }


async def _set_stage(deps: PipelineDeps, target: VersionToIngest, stage: str) -> None:
    async with tenant_transaction(deps.session_factory, target.organisation_id) as s:
        await update_job(s, target.job_id, stage=stage)


async def _finish(
    deps: PipelineDeps,
    target: VersionToIngest,
    status: IngestionStatus,
    *,
    error_code: str | None = None,
    chunk_count: int | None = None,
) -> IngestionStatus:
    values: dict[str, object] = {"status": status, "error_code": error_code}
    if status is not IngestionStatus.QUEUED:  # queued again means a retry is coming
        values["finished_at"] = datetime.now(UTC)
    if status is IngestionStatus.SUCCEEDED:
        values.update(stage="done", chunk_count=chunk_count)
    async with tenant_transaction(deps.session_factory, target.organisation_id) as s:
        await update_job(s, target.job_id, **values)
    logger.info("ingestion_finished", status=status.value, error_code=error_code)
    return status
