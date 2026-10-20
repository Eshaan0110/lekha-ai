"""FastAPI application factory.

Run locally with ``./dev.ps1 run`` (uvicorn ``apps.api.main:create_app --factory``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from apps.api import health, routes_v1, search, uploads
from apps.api.audit import AuditMiddleware
from apps.api.errors import register_error_handlers
from apps.api.middleware import RequestContextMiddleware
from apps.api.readiness import build_readiness_checks
from apps.wiring import ArqJobQueue, JobQueue, build_embedder, build_vector_index
from db.audit import PostgresAuditSink
from db.engine import create_engine, create_session_factory
from db.idempotency import IdempotencyStore
from lekha.audit import AuditSink
from lekha.auth import DevIdentityProvider, IdentityProvider
from lekha.core.config import AuthMode, Environment, Settings
from lekha.core.health import ReadinessCheck
from lekha.core.log import configure_logging
from lekha.memory import ObjectStore
from lekha.memory.s3_store import S3ObjectStore

EXTERNAL_CALL_TIMEOUT = httpx.Timeout(5.0, connect=2.0)
IDEMPOTENCY_TTL = timedelta(hours=24)
# An in-progress claim this old is treated as abandoned (e.g. the server crashed mid-request).
IDEMPOTENCY_STALE_AFTER = timedelta(minutes=5)

logger = structlog.get_logger("lekha.api")


def create_app(
    settings: Settings | None = None,
    readiness_checks: Sequence[ReadinessCheck] | None = None,
    audit_sink: AuditSink | None = None,
    object_store: ObjectStore | None = None,
    job_queue: JobQueue | None = None,
) -> FastAPI:
    """Build the API. Tests may inject settings, readiness checks, audit sink and store."""
    settings = settings or Settings()  # values come from the environment
    configure_logging(settings)
    identity_provider = _build_identity_provider(settings)
    embedder = build_embedder(settings)  # fails at startup if bge-m3 is chosen but absent

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Clients are created lazily: nothing connects until first use, so the API starts
        # even if a dependency is down (readiness reports it instead).
        engine = create_engine(settings)
        redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password.get_secret_value(),
            socket_connect_timeout=2,
            socket_timeout=5,
        )
        http = httpx.AsyncClient(timeout=EXTERNAL_CALL_TIMEOUT, follow_redirects=False)
        store = object_store or S3ObjectStore.from_settings(settings)

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.audit_sink = audit_sink or PostgresAuditSink(app.state.session_factory)
        app.state.identity_provider = identity_provider
        app.state.object_store = store
        app.state.embedder = embedder
        app.state.vector_index = build_vector_index(settings, embedder.dimensions)
        app.state.job_queue = job_queue or ArqJobQueue(settings)
        app.state.idempotency = IdempotencyStore(
            app.state.session_factory, ttl=IDEMPOTENCY_TTL, stale_after=IDEMPOTENCY_STALE_AFTER
        )
        app.state.readiness_checks = (
            list(readiness_checks)
            if readiness_checks is not None
            else build_readiness_checks(settings, engine, redis, http, store)
        )
        logger.info("api_started", environment=settings.environment.value)
        try:
            yield
        finally:
            await http.aclose()
            await app.state.vector_index.close()
            await app.state.job_queue.close()
            await redis.aclose()
            await engine.dispose()
            logger.info("api_stopped")

    interactive_docs = settings.environment in {Environment.LOCAL, Environment.TEST}
    app = FastAPI(
        title="Lekha API",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are a local convenience; the schema is exported for other envs.
        docs_url="/docs" if interactive_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if interactive_docs else None,
    )
    # Starlette runs the last-added middleware first: request IDs wrap auditing, so every
    # audit event carries the request ID and audit-failure responses still get the header.
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(routes_v1.router)
    app.include_router(uploads.router)
    app.include_router(search.router)
    return app


def _build_identity_provider(settings: Settings) -> IdentityProvider | None:
    """Built at startup, so a forbidden configuration stops the app instead of a request."""
    if settings.auth_mode is AuthMode.DEV and settings.dev_auth_organisation_id is not None:
        # (Settings already refuses dev mode without an organisation ID.)
        logger.warning("dev_sign_in_enabled", environment=settings.environment.value)
        return DevIdentityProvider(
            environment=settings.environment.value,
            organisation_id=settings.dev_auth_organisation_id,
        )
    # Group OIDC is blocked on Phase 0 (IdP access). Until it exists, every protected route
    # answers 401: failing closed rather than open.
    logger.warning("oidc_not_implemented_all_protected_routes_refuse")
    return None
