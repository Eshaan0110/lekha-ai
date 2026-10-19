"""Create the least-privilege group role the API runs under.

``lekha_app_rw`` cannot log in. A login account (``LEKHA_DB_APP_USER``) is made a member of
it by ``apps/scripts/provision_db_app_user.py``, so passwords never appear in migrations.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "lekha_app_rw"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_GROUP_ROLE}') THEN
                CREATE ROLE {APP_GROUP_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                    NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    # Nobody but the owner may create objects in the schema.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_GROUP_ROLE}")
    # Tables created later by the migration owner are readable/writable by the app (no DDL).
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_GROUP_ROLE}"
    )


def downgrade() -> None:
    # Roles are cluster-wide, shared by every database on the server (e.g. the dev and
    # test databases), so the role itself is left in place; only this database's grants go.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_GROUP_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_GROUP_ROLE}")
