"""Actions: proposals, approvals and executions (plan "Safe Actions"), plus the simulated
ticketing system, all with tenant row-level security (Phase 5 step 1).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("simulated_ticket", "action_proposal", "action_approval", "action_execution")


def upgrade() -> None:
    op.create_table(
        "simulated_ticket",
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reference ~ '^SIM-[0-9A-F]{8}$'", name=op.f("ck_simulated_ticket_reference_format")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_simulated_ticket_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulated_ticket")),
        sa.UniqueConstraint("organisation_id", "idempotency_key", name="uq_simulated_ticket_key"),
        sa.UniqueConstraint("organisation_id", "reference", name="uq_simulated_ticket_reference"),
    )
    op.create_table(
        "action_proposal",
        sa.Column("business_unit_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("derived_from", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposed_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_approval",
                "approved",
                "rejected",
                "executed",
                "failed",
                "expired",
                name="proposal_status",
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("tool IN ('create_ticket')", name=op.f("ck_action_proposal_tool_known")),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 200",
            name=op.f("ck_action_proposal_key_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_action_proposal_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "proposed_by"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_action_proposal_proposer",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_action_proposal_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_proposal")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_action_proposal_org_id"),
        sa.UniqueConstraint("organisation_id", "idempotency_key", name="uq_action_proposal_key"),
    )
    op.create_index(
        "ix_action_proposal_unit_status",
        "action_proposal",
        ["business_unit_id", "status"],
        unique=False,
    )
    op.create_table(
        "action_approval",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("approver_id", sa.Uuid(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("edited_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(reason) <= 500", name=op.f("ck_action_approval_reason_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "approver_id"],
            ["app_user.organisation_id", "app_user.id"],
            name="fk_action_approval_approver",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "proposal_id"],
            ["action_proposal.organisation_id", "action_proposal.id"],
            name=op.f("fk_action_approval_organisation_id_action_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_action_approval_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_approval")),
        sa.UniqueConstraint("proposal_id", name="uq_action_approval_proposal"),
    )
    op.create_table(
        "action_execution",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
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
            name=op.f("ck_action_execution_error_code_format"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "proposal_id"],
            ["action_proposal.organisation_id", "action_proposal.id"],
            name=op.f("fk_action_execution_organisation_id_action_proposal"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_action_execution_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_execution")),
        sa.UniqueConstraint("organisation_id", "idempotency_key", name="uq_action_execution_key"),
        sa.UniqueConstraint("proposal_id", name="uq_action_execution_proposal"),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("action_execution")
    op.drop_table("action_approval")
    op.drop_index("ix_action_proposal_unit_status", table_name="action_proposal")
    op.drop_table("action_proposal")
    op.drop_table("simulated_ticket")
    sa.Enum(name="proposal_status").drop(op.get_bind(), checkfirst=False)
