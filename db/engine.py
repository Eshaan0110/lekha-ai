"""SQLAlchemy engine and session factory.

Transaction boundaries are explicit: callers open a session with
``async with session_factory.begin() as session:`` so that the whole unit of work
commits or rolls back together.
"""

from __future__ import annotations

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from lekha.core.config import Settings


def database_url(settings: Settings, *, owner: bool = False) -> URL:
    """asyncpg URL for the least-privilege app account, or the owner account (migrations).

    The same driver serves the API and Alembic.
    """
    if owner:
        username, password = settings.postgres_user, settings.postgres_password
    else:
        username, password = settings.db_app_user, settings.db_app_password
    return URL.create(
        drivername="postgresql+asyncpg",
        username=username,
        password=password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        database_url(settings),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,  # transparently replace connections dropped by the server
        # Keep bound values (user input, personal data, secrets) out of error messages/logs.
        hide_parameters=True,
        pool_timeout=10,
        connect_args={
            "timeout": 5,  # seconds to establish a connection
            "server_settings": {
                # Server-side cap so a runaway query cannot hold a connection forever.
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "application_name": "lekha",
            },
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
