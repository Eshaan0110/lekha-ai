"""Ingestion jobs (per-version pipeline status) and chunks, with tenant row-level security.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("heading_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('en', 'hi', 'kn', 'und')", name=op.f("ck_chunk_language_known")
        ),
        sa.CheckConstraint("octet_length(text_sha256) = 32", name=op.f("ck_chunk_sha256_length")),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_chunk_ordinal_non_negative")),
        sa.CheckConstraint("token_count > 0", name=op.f("ck_chunk_token_count_positive")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name=op.f("fk_chunk_organisation_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_chunk_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk")),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_chunk_version_ordinal"),
    )
    op.create_table(
        "ingestion_job",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "succeeded", "failed", name="ingestion_status"),
            nullable=False,
        ),
        sa.Column("stage", sa.Text(), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "error_code IS NULL OR error_code ~ '^[a-z_]{1,60}$'",
            name=op.f("ck_ingestion_job_error_code"),
        ),
        sa.CheckConstraint(
            "stage IN ('queued', 'extract', 'chunk', 'embed', 'index', 'done')",
            name=op.f("ck_ingestion_job_stage_known"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_ingestion_job_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name=op.f("fk_ingestion_job_organisation_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_ingestion_job_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_job")),
        sa.UniqueConstraint("document_version_id", name="uq_ingestion_job_version"),
        sa.UniqueConstraint("organisation_id", "id", name="uq_ingestion_job_org_id"),
    )

    for table in ("chunk", "ingestion_job"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("ingestion_job")
    op.drop_table("chunk")
    sa.Enum(name="ingestion_status").drop(op.get_bind(), checkfirst=False)
