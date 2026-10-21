"""Question and answer logs (query_log, answer_log, citation), with tenant row-level
security (Phase 3: every answer is traceable to its sources).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_log",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('en', 'hi', 'kn', 'und')", name=op.f("ck_query_log_language_known")
        ),
        sa.CheckConstraint(
            "char_length(question) BETWEEN 1 AND 1000", name=op.f("ck_query_log_question_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_query_log_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_query_log_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_log")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_query_log_org_id"),
    )
    op.create_table(
        "answer_log",
        sa.Column("query_log_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("abstention_reason", sa.Text(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("dropped_citations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "abstention_reason IS NULL OR abstention_reason ~ '^[a-z_]{1,60}$'",
            name=op.f("ck_answer_log_abstention_reason_format"),
        ),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_answer_log_latency_non_negative")),
        sa.CheckConstraint(
            "prompt_sha256 IS NULL OR octet_length(prompt_sha256) = 32",
            name=op.f("ck_answer_log_prompt_sha256_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "query_log_id"],
            ["query_log.organisation_id", "query_log.id"],
            name=op.f("fk_answer_log_organisation_id_query_log"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_answer_log_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_log")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_answer_log_org_id"),
        sa.UniqueConstraint("query_log_id", name="uq_answer_log_query"),
    )
    op.create_table(
        "citation",
        sa.Column("answer_log_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("source_number", sa.Integer(), nullable=False),
        sa.Column("span", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source_number > 0", name=op.f("ck_citation_source_number_positive")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "answer_log_id"],
            ["answer_log.organisation_id", "answer_log.id"],
            name=op.f("fk_citation_organisation_id_answer_log"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name=op.f("fk_citation_organisation_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_citation_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation")),
    )
    for table in ("query_log", "answer_log", "citation"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("citation")
    op.drop_table("answer_log")
    op.drop_table("query_log")
