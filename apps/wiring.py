"""Builds infrastructure adapters from settings; shared by the API and the worker."""

from __future__ import annotations

import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from lekha.core.config import Settings
from lekha.retrieval import QdrantIndex
from model_layer import (
    BedrockClaudeModel,
    BgeM3Embedder,
    Embedder,
    FakeEmbedder,
    LanguageModel,
    MiniLmEmbedder,
    OllamaModel,
)

INGEST_FUNCTION = "ingest"
PURGE_FUNCTION = "purge"
ACTION_FUNCTION = "execute_action"


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "bge-m3":
        return BgeM3Embedder(settings.embedder_model_dir, device=settings.embedder_device)
    if settings.embedder == "minilm":
        return MiniLmEmbedder(settings.minilm_model_dir)
    return FakeEmbedder()


def build_language_model(settings: Settings) -> LanguageModel | None:
    """None when no model is configured: /api/v1/ask then answers 503."""
    if settings.llm == "ollama":
        return OllamaModel(
            str(settings.ollama_url),
            settings.ollama_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm == "bedrock":
        return BedrockClaudeModel.connect(
            settings.bedrock_region,
            settings.bedrock_model_id,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return None


def build_vector_index(settings: Settings, dimensions: int) -> QdrantIndex:
    """``dimensions`` comes from the embedder; a collection of another size is refused."""
    return QdrantIndex.connect(
        str(settings.qdrant_url),
        settings.qdrant_api_key.get_secret_value(),
        settings.qdrant_collection,
        dimensions,
    )


def queue_name(settings: Settings) -> str:
    """One queue per database, so test jobs never reach the dev worker (and vice versa)."""
    return f"lekha:{settings.postgres_db}:ingest"


def redis_settings(settings: Settings) -> RedisSettings:
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        conn_timeout=2,
    )


class JobQueue(Protocol):
    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None: ...

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None: ...

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None: ...

    async def enqueue_action(self, organisation_id: uuid.UUID, proposal_id: uuid.UUID) -> None: ...

    async def close(self) -> None: ...


class ArqJobQueue:
    """Enqueues ingestion jobs on Redis; connects lazily so the API starts without Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ArqRedis | None = None

    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        pool = await self._get_pool()
        # The job ID doubles as ARQ's job ID, so enqueueing the same job twice is a no-op.
        await pool.enqueue_job(
            INGEST_FUNCTION,
            str(organisation_id),
            str(job_id),
            _job_id=f"ingest:{job_id}",
            _queue_name=queue_name(self._settings),
        )

    async def enqueue_purge(self, organisation_id: uuid.UUID, request_id: uuid.UUID) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(
            PURGE_FUNCTION,
            str(organisation_id),
            str(request_id),
            _job_id=f"purge:{request_id}",
            _queue_name=queue_name(self._settings),
        )

    async def enqueue_reindex(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """Run an already-finished ingestion job again (e.g. approved minutes to add). A fresh
        queue ID, because ARQ ignores a job ID it has seen recently."""
        pool = await self._get_pool()
        await pool.enqueue_job(
            INGEST_FUNCTION,
            str(organisation_id),
            str(job_id),
            _job_id=f"reindex:{job_id}:{uuid.uuid4().hex}",
            _queue_name=queue_name(self._settings),
        )

    async def enqueue_action(self, organisation_id: uuid.UUID, proposal_id: uuid.UUID) -> None:
        """Execute an approved proposal. The proposal ID is the queue ID, so a second enqueue
        is a no-op; the executor's idempotency key guards beyond ARQ's memory."""
        pool = await self._get_pool()
        await pool.enqueue_job(
            ACTION_FUNCTION,
            str(organisation_id),
            str(proposal_id),
            _job_id=f"action:{proposal_id}",
            _queue_name=queue_name(self._settings),
        )

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(redis_settings(self._settings))
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
