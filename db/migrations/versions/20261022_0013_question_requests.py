"""Question requests: unanswered questions people chose to log for their curators, with
tenant row-level security (Phase 4 pilot support).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_request",
        sa.Column("query_log_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("business_unit_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", "dismissed", name="request_status"),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text(), server_default="", nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'open') = (resolved_at IS NULL)",
            name=op.f("ck_question_request_resolved_unless_open"),
        ),
        sa.CheckConstraint(
            "char_length(note) <= 500", name=op.f("ck_question_request_note_length")
        ),
        sa.CheckConstraint(
            "char_length(resolution) <= 500", name=op.f("ck_question_request_resolution_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_question_request_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "query_log_id"],
            ["query_log.organisation_id", "query_log.id"],
            name=op.f("fk_question_request_organisation_id_query_log"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "resolved_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_question_request_resolver",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_question_request_asker",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_question_request_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_request")),
        sa.UniqueConstraint("query_log_id", name="uq_question_request_query"),
    )
    op.create_index(
        "ix_question_request_unit_status",
        "question_request",
        ["business_unit_id", "status"],
        unique=False,
    )
    op.execute("ALTER TABLE question_request ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON question_request "
        "USING (organisation_id = lekha_current_tenant()) "
        "WITH CHECK (organisation_id = lekha_current_tenant())"
    )


def downgrade() -> None:
    op.drop_index("ix_question_request_unit_status", table_name="question_request")
    op.drop_table("question_request")
    sa.Enum(name="request_status").drop(op.get_bind(), checkfirst=False)
