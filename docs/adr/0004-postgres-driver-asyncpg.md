# ADR-0004: asyncpg as the single PostgreSQL driver

- **Status:** Proposed
- **Date:** 2026-10-17

## Context
FastAPI is asynchronous, so database access should not block the event loop. The first
choice, psycopg 3 in async mode, cannot run on Windows' default `ProactorEventLoop`. That
would break local development and tests on the reference machine.

## Decision
Use `asyncpg` through SQLAlchemy 2 (`postgresql+asyncpg`) for both the API and Alembic
(Alembic runs migrations through `connection.run_sync`). Connection settings: 5-second
connect timeout, server-side `statement_timeout` (15 s by default), and `pool_pre_ping`.

## Alternatives considered
- **psycopg 3 async with a forced `WindowsSelectorEventLoopPolicy`**: works, but needs
  event-loop patching in every entry point and test runner, and that API is deprecated
  from Python 3.14.
- **psycopg 3, synchronous only**: simpler, but blocks worker threads under load.
- **Two drivers (asyncpg for the app, psycopg for Alembic)**: more dependencies for no benefit.

## Why it fits Lekha
One driver everywhere; it works on Windows and Linux and performs well.

## Trade-offs
asyncpg's prepared-statement cache needs configuration if PgBouncer in transaction mode
is introduced later.

## Security and operational implications
All queries stay parameterized through SQLAlchemy. The statement timeout bounds runaway
queries.

## Cost
Free.

## Revisit when
A connection pooler such as PgBouncer or RDS Proxy is introduced.
