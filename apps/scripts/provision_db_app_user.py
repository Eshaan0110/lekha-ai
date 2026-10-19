"""Create or update the API's least-privilege database login.

Run after migrations (``./dev.ps1 migrate`` does this). It connects as the owner account,
creates ``LEKHA_DB_APP_USER`` if needed, sets its password from ``LEKHA_DB_APP_PASSWORD``,
and makes it a member of the ``lekha_app_rw`` group role created by migration 0001.

In production this is done by infrastructure tooling with secrets from the secrets manager.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from db.engine import database_url
from lekha.core.config import Settings

APP_GROUP_ROLE = "lekha_app_rw"


class ProvisioningError(RuntimeError):
    """Raised with a sanitised message (no SQL text, no secrets)."""


async def _execute_formatted(conn: AsyncConnection, template: str, *args: str) -> None:
    """Run DDL built by the server's ``format()``, which quotes identifiers (%I) and
    literals (%L) safely. DDL statements cannot take bind parameters directly."""
    params = {f"a{i}": arg for i, arg in enumerate(args)}
    placeholders = ", ".join(f"CAST(:a{i} AS text)" for i in range(len(args)))
    statement = (
        await conn.execute(text(f"SELECT format('{template}', {placeholders})"), params)
    ).scalar_one()
    try:
        await conn.execute(text(statement))
    except DBAPIError as exc:
        # The statement text contains the password literal; never let it reach output.
        raise ProvisioningError(
            f"provisioning statement failed ({type(exc.orig).__name__})"
        ) from None


async def provision(settings: Settings) -> None:
    # hide_parameters: a failing statement must never print the password it carried.
    engine = create_async_engine(
        database_url(settings, owner=True), poolclass=NullPool, hide_parameters=True
    )
    user = settings.db_app_user
    password = settings.db_app_password.get_secret_value()
    try:
        async with engine.begin() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": user}
                )
            ).first()
            verb = "CREATE" if exists is None else "ALTER"
            await _execute_formatted(
                conn,
                f"{verb} ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L",
                user,
                password,
            )
            await _execute_formatted(conn, "GRANT %I TO %I", APP_GROUP_ROLE, user)
    finally:
        await engine.dispose()
    print(f"Database login '{user}' provisioned as a member of {APP_GROUP_ROLE}.")


def main() -> None:
    asyncio.run(provision(Settings()))


if __name__ == "__main__":
    main()
