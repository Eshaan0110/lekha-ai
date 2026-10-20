"""Deletion requests (plan deletion workflow), with tenant row-level security.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deletion_request",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "completed", "failed", name="deletion_status"),
            nullable=False,
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("chunks_removed", sa.Integer(), nullable=True),
        sa.Column("files_removed", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_deletion_request_completed_has_time"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z_]{1,60}$'",
            name=op.f("ck_deletion_request_error_code"),
        ),
        sa.CheckConstraint("scope IN ('document')", name=op.f("ck_deletion_request_scope_known")),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_id"],
            ["document.organisation_id", "document.id"],
            name=op.f("fk_deletion_request_organisation_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "requested_by"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_deletion_request_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_deletion_request_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_request")),
        sa.UniqueConstraint("organisation_id", "document_id", name="uq_deletion_request_document"),
        sa.UniqueConstraint("organisation_id", "id", name="uq_deletion_request_org_id"),
    )
    op.execute("ALTER TABLE deletion_request ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON deletion_request "
        "USING (organisation_id = lekha_current_tenant()) "
        "WITH CHECK (organisation_id = lekha_current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("deletion_request")
    sa.Enum(name="deletion_status").drop(op.get_bind(), checkfirst=False)
