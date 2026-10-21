"""Answer feedback (was this answer helpful?), with tenant row-level security.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("query_log_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) <= 1000",
            name=op.f("ck_answer_feedback_comment_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "query_log_id"],
            ["query_log.organisation_id", "query_log.id"],
            name=op.f("fk_answer_feedback_organisation_id_query_log"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_answer_feedback_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_answer_feedback_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_feedback")),
        sa.UniqueConstraint("query_log_id", name="uq_answer_feedback_query"),
    )
    op.execute("ALTER TABLE answer_feedback ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON answer_feedback "
        "USING (organisation_id = lekha_current_tenant()) "
        "WITH CHECK (organisation_id = lekha_current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("answer_feedback")
