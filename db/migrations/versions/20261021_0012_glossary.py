"""The shared glossary: entries and the names each goes by, with tenant row-level security
(Phase 4).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_entry",
        sa.Column("business_unit_id", sa.Uuid(), nullable=False),
        sa.Column("canonical", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("shared", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(canonical) BETWEEN 1 AND 120",
            name=op.f("ck_glossary_entry_canonical_length"),
        ),
        sa.CheckConstraint(
            "char_length(description) <= 500", name=op.f("ck_glossary_entry_description_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_glossary_entry_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "created_by"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_glossary_entry_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_glossary_entry_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_entry")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_glossary_entry_org_id"),
    )
    op.create_table(
        "glossary_name",
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120", name=op.f("ck_glossary_name_name_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "entry_id"],
            ["glossary_entry.organisation_id", "glossary_entry.id"],
            name=op.f("fk_glossary_name_organisation_id_glossary_entry"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_glossary_name_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_name")),
        sa.UniqueConstraint("organisation_id", "name_norm", name="uq_glossary_name_norm"),
    )
    op.create_index("ix_glossary_name_entry", "glossary_name", ["entry_id"], unique=False)
    for table in ("glossary_entry", "glossary_name"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_index("ix_glossary_name_entry", table_name="glossary_name")
    op.drop_table("glossary_name")
    op.drop_table("glossary_entry")
