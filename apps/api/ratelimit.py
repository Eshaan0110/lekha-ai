"""Per-user rate limit for expensive endpoints (plan API conventions: X-RateLimit-* headers).

A fixed one-minute window in Redis. If Redis can't be reached the request is refused (503)
rather than let through unmetered: answers cost model time, and later real money.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int  # seconds until the window resets


class RateLimiterUnavailableError(RuntimeError):
    """The counter store could not be reached."""


class RateLimiter(Protocol):
    async def hit(self, scope: str, user_id: uuid.UUID) -> RateDecision: ...


class RedisRateLimiter:
    def __init__(self, redis: Redis, *, namespace: str, limit_per_minute: int) -> None:
        self._redis = redis
        self._namespace = namespace
        self._limit = limit_per_minute

    async def hit(self, scope: str, user_id: uuid.UUID) -> RateDecision:
        now = int(time.time())
        window = now // WINDOW_SECONDS
        key = f"{self._namespace}:ratelimit:{scope}:{user_id}:{window}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, WINDOW_SECONDS * 2)
                count, _ = await pipe.execute()
        except (RedisError, OSError):
            raise RateLimiterUnavailableError("rate limit store unavailable") from None
        used = int(count)
        return RateDecision(
            allowed=used <= self._limit,
            limit=self._limit,
            remaining=max(self._limit - used, 0),
            retry_after=WINDOW_SECONDS - now % WINDOW_SECONDS,
        )
