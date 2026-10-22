"""The phone channel: HR contact details, calls and transcripts, one-time codes, the
simulated SMS outbox and the hand-over queue, all with tenant row-level security
(Phase 5 step 2).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "employee_contact",
    "outbox_message",
    "voice_call",
    "escalation",
    "verification_code",
    "voice_turn",
)


def upgrade() -> None:
    op.create_table(
        "employee_contact",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("hr_number", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "hr_number ~ '^MG-[0-9]{5}$'", name=op.f("ck_employee_contact_hr_number_format")
        ),
        sa.CheckConstraint(
            "phone ~ '^\\+[0-9]{8,15}$'", name=op.f("ck_employee_contact_phone_format")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_employee_contact_user",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_employee_contact_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_contact")),
        sa.UniqueConstraint("organisation_id", "hr_number", name="uq_employee_contact_hr_number"),
        sa.UniqueConstraint("user_id", name="uq_employee_contact_user"),
    )
    op.create_table(
        "outbox_message",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('verification_code', 'answer', 'ticket')",
            name=op.f("ck_outbox_message_kind_known"),
        ),
        sa.CheckConstraint("char_length(body) <= 4000", name=op.f("ck_outbox_message_body_length")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_outbox_message_user",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_outbox_message_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_message")),
    )
    op.create_index("ix_outbox_message_created", "outbox_message", ["created_at"], unique=False)
    op.create_table(
        "voice_call",
        sa.Column("caller_number", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "awaiting_id",
                "awaiting_code",
                "ready",
                "describing_issue",
                "confirming_ticket",
                "escalated",
                "ended",
                name="call_state",
            ),
            nullable=False,
        ),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("claimed_user_id", sa.Uuid(), nullable=True),
        sa.Column("verified_user_id", sa.Uuid(), nullable=True),
        sa.Column("id_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("code_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pending_ticket", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state IN ('escalated', 'ended')) = (ended_at IS NOT NULL)",
            name=op.f("ck_voice_call_ended_when_closed"),
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason ~ '^[a-z_]{1,40}$'",
            name=op.f("ck_voice_call_end_reason_format"),
        ),
        sa.CheckConstraint(
            "language IS NULL OR language IN ('en', 'hi', 'kn')",
            name=op.f("ck_voice_call_language"),
        ),
        sa.CheckConstraint(
            "char_length(caller_number) <= 32", name=op.f("ck_voice_call_caller_number_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "claimed_user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_voice_call_claimed_user",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "verified_user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_voice_call_verified_user",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_voice_call_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_call")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_voice_call_org_id"),
    )
    op.create_table(
        "escalation",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason IN ('caller_request', 'upset', 'out_of_scope', 'system_failure', 'verification_failed')",
            name=op.f("ck_escalation_reason_known"),
        ),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_escalation_status_known")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "call_id"],
            ["voice_call.organisation_id", "voice_call.id"],
            name="fk_escalation_call",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_escalation_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalation")),
        sa.UniqueConstraint("call_id", name="uq_escalation_call"),
    )
    op.create_table(
        "verification_code",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("salt", sa.LargeBinary(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "call_id"],
            ["voice_call.organisation_id", "voice_call.id"],
            name="fk_verification_code_call",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_verification_code_user",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_verification_code_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_code")),
    )
    op.create_index(
        "ix_verification_code_user_created",
        "verification_code",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "voice_turn",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("speaker IN ('caller', 'lekha')", name=op.f("ck_voice_turn_speaker")),
        sa.CheckConstraint("char_length(text) <= 4000", name=op.f("ck_voice_turn_text_length")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "call_id"],
            ["voice_call.organisation_id", "voice_call.id"],
            name="fk_voice_turn_call",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_voice_turn_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_turn")),
        sa.UniqueConstraint("call_id", "seq", name="uq_voice_turn_seq"),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("voice_turn")
    op.drop_index("ix_verification_code_user_created", table_name="verification_code")
    op.drop_table("verification_code")
    op.drop_table("escalation")
    op.drop_table("voice_call")
    op.drop_index("ix_outbox_message_created", table_name="outbox_message")
    op.drop_table("outbox_message")
    op.drop_table("employee_contact")
    sa.Enum(name="call_state").drop(op.get_bind(), checkfirst=False)
