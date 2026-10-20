"""ARQ worker entry point: ``./dev.ps1 worker`` (runs ``arq apps.worker.main.WorkerSettings``)."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from arq import Retry

from apps.wiring import build_embedder, build_vector_index, queue_name, redis_settings
from apps.worker.pipeline import PipelineDeps, TransientIngestionError, run_ingestion
from db.engine import create_engine, create_session_factory
from lekha.core.config import Settings
from lekha.core.log import configure_logging
from lekha.memory.s3_store import S3ObjectStore

MAX_TRIES = 4
_SETTINGS = Settings()


async def startup(ctx: dict[str, Any]) -> None:  # Any: ARQ's context dict
    configure_logging(_SETTINGS)
    engine = create_engine(_SETTINGS)
    ctx["engine"] = engine
    embedder = build_embedder(_SETTINGS)
    ctx["deps"] = PipelineDeps(
        session_factory=create_session_factory(engine),
        store=S3ObjectStore.from_settings(_SETTINGS),
        index=build_vector_index(_SETTINGS, embedder.dimensions),
        embedder=embedder,
    )


async def shutdown(ctx: dict[str, Any]) -> None:  # Any: ARQ's context dict
    await ctx["deps"].index.close()
    await ctx["engine"].dispose()


async def ingest(ctx: dict[str, Any], organisation_id: str, job_id: str) -> str | None:
    try:
        status = await run_ingestion(
            ctx["deps"],
            uuid.UUID(organisation_id),
            uuid.UUID(job_id),
            final_attempt=ctx["job_try"] >= MAX_TRIES,
        )
    except TransientIngestionError:
        # Back off 10 s, 20 s, 30 s between attempts.
        raise Retry(defer=10 * ctx["job_try"]) from None
    return status.value if status is not None else None


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ingest]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(_SETTINGS)
    queue_name = queue_name(_SETTINGS)
    max_tries = MAX_TRIES
    job_timeout = 600
