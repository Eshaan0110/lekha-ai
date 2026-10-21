"""Meetings and participants (Phase 4 meeting memory) with tenant row-level security, and
start/end times on chunks so citations can point at a moment in a meeting.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meeting",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organiser_user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source IN ('vtt', 'srt', 'txt')", name=op.f("ck_meeting_source_known")),
        sa.CheckConstraint(
            "external_id IS NULL OR char_length(external_id) BETWEEN 1 AND 200",
            name=op.f("ck_meeting_external_id_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_id"],
            ["document.organisation_id", "document.id"],
            name=op.f("fk_meeting_organisation_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "organiser_user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_meeting_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_meeting_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting")),
        sa.UniqueConstraint("document_id", name="uq_meeting_document"),
        sa.UniqueConstraint("organisation_id", "id", name="uq_meeting_org_id"),
        sa.UniqueConstraint("organisation_id", "source", "external_id", name="uq_meeting_external"),
    )
    op.create_index(
        "ix_meeting_organiser_started", "meeting", ["organiser_user_id", "started_at"], unique=False
    )
    op.create_table(
        "meeting_participant",
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "meeting_id"],
            ["meeting.organisation_id", "meeting.id"],
            name=op.f("fk_meeting_participant_organisation_id_meeting"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_meeting_participant_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_meeting_participant_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meeting_participant")),
        sa.UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant"),
    )
    op.add_column("chunk", sa.Column("start_ms", sa.Integer(), nullable=True))
    op.add_column("chunk", sa.Column("end_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_chunk_times_ordered",
        "chunk",
        "(start_ms IS NULL OR start_ms >= 0) AND "
        "(end_ms IS NULL OR start_ms IS NULL OR end_ms >= start_ms)",
    )
    for table in ("meeting", "meeting_participant"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_constraint("ck_chunk_times_ordered", "chunk", type_="check")
    op.drop_column("chunk", "end_ms")
    op.drop_column("chunk", "start_ms")
    op.drop_table("meeting_participant")
    op.drop_index("ix_meeting_organiser_started", table_name="meeting")
    op.drop_table("meeting")
