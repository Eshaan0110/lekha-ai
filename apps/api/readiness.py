"""Concrete readiness probes for the API's infrastructure dependencies."""

from __future__ import annotations

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lekha.core.config import Settings
from lekha.core.health import ReadinessCheck
from lekha.memory import ObjectStore


def build_readiness_checks(
    settings: Settings,
    engine: AsyncEngine,
    redis: Redis,
    http: httpx.AsyncClient,
    object_store: ObjectStore,
) -> list[ReadinessCheck]:
    async def postgres() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def redis_ping() -> None:
        await redis.ping()

    async def qdrant() -> None:
        # An authenticated call, so a wrong API key also marks the service not ready.
        response = await http.get(
            f"{str(settings.qdrant_url).rstrip('/')}/collections",
            headers={"api-key": settings.qdrant_api_key.get_secret_value()},
        )
        response.raise_for_status()

    async def object_storage() -> None:
        # HEAD on the document bucket: proves reachability, credentials and the bucket.
        await object_store.check()

    return [
        ReadinessCheck("postgres", postgres),
        ReadinessCheck("redis", redis_ping),
        ReadinessCheck("qdrant", qdrant),
        ReadinessCheck("object_storage", object_storage),
    ]
