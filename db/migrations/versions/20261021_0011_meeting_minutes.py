"""Meeting minutes drafted from a transcript and approved by the organiser, with tenant
row-level security (Phase 4, plan F-12).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meeting_minutes",
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("transcript_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "approved", "rejected", "superseded", name="minutes_status"),
            nullable=False,
        ),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dropped_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("drafted_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'draft') = (decided_at IS NULL)",
            name=op.f("ck_meeting_minutes_decided_unless_draft"),
        ),
        sa.CheckConstraint(
            "template IN ('standard', 'brief')", name=op.f("ck_meeting_minutes_template_known")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "decided_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_meeting_minutes_decided_by",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "drafted_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_meeting_minutes_drafted_by",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "meeting_id"],
            ["meeting.organisation_id", "meeting.id"],
            name=op.f("fk_meeting_minutes_organisation_id_meeting"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "transcript_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name=op.f("fk_meeting_minutes_organisation_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_meeting_minutes_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting_minutes")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_meeting_minutes_org_id"),
    )
    op.create_index("ix_meeting_minutes_meeting", "meeting_minutes", ["meeting_id"], unique=False)
    op.execute("ALTER TABLE meeting_minutes ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON meeting_minutes "
        "USING (organisation_id = lekha_current_tenant()) "
        "WITH CHECK (organisation_id = lekha_current_tenant())"
    )


def downgrade() -> None:
    op.drop_index("ix_meeting_minutes_meeting", table_name="meeting_minutes")
    op.drop_table("meeting_minutes")
    sa.Enum(name="minutes_status").drop(op.get_bind(), checkfirst=False)
