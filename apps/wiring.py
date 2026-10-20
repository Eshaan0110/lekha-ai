"""Builds infrastructure adapters from settings; shared by the API and the worker."""

from __future__ import annotations

import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from lekha.core.config import Settings
from lekha.retrieval import QdrantIndex
from model_layer import BgeM3Embedder, Embedder, FakeEmbedder, MiniLmEmbedder

INGEST_FUNCTION = "ingest"


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "bge-m3":
        return BgeM3Embedder(settings.embedder_model_dir, device=settings.embedder_device)
    if settings.embedder == "minilm":
        return MiniLmEmbedder(settings.minilm_model_dir)
    return FakeEmbedder()


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

    async def close(self) -> None: ...


class ArqJobQueue:
    """Enqueues ingestion jobs on Redis; connects lazily so the API starts without Redis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ArqRedis | None = None

    async def enqueue_ingestion(self, organisation_id: uuid.UUID, job_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(redis_settings(self._settings))
        # The job ID doubles as ARQ's job ID, so enqueueing the same job twice is a no-op.
        await self._pool.enqueue_job(
            INGEST_FUNCTION,
            str(organisation_id),
            str(job_id),
            _job_id=f"ingest:{job_id}",
            _queue_name=queue_name(self._settings),
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
