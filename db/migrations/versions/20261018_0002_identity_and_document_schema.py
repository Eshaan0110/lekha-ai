"""Identity and document schema with tenant row-level security.

Tables follow docs/design/permissions-v1.md. Every tenant table carries organisation_id;
composite foreign keys stop cross-tenant references, and row-level security limits the app
role to the organisation set in ``lekha.tenant_id`` for the current transaction.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_GROUP_ROLE = "lekha_app_rw"

# Declaration order matters for classification: it defines public < ... < restricted.
ENUMS = {
    "classification": ("public", "internal", "confidential", "restricted"),
    "subject_kind": ("user", "role", "group"),
    "access_level": ("read", "comment", "curate"),
    "acl_source": ("explicit", "inherited_from_source", "role_implicit"),
}

TENANT_TABLES = (
    "business_unit",
    "app_user",
    "role",
    "user_role_grant",
    "permission_subject",
    "source_system",
    "document",
    "document_file",
    "document_version",
    "document_acl",
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=False)

    op.create_table(
        "organisation",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200", name=op.f("ck_organisation_name_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisation")),
        sa.UniqueConstraint("name", name=op.f("uq_organisation_name")),
    )
    op.create_table(
        "app_user",
        sa.Column("idp_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            "char_length(display_name) BETWEEN 1 AND 200",
            name=op.f("ck_app_user_display_name_length"),
        ),
        sa.CheckConstraint(
            "char_length(idp_subject) BETWEEN 1 AND 255",
            name=op.f("ck_app_user_idp_subject_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_app_user_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_app_user_org_id"),
        sa.UniqueConstraint("organisation_id", "idp_subject", name="uq_app_user_org_idp_subject"),
    )
    op.create_table(
        "business_unit",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name=op.f("ck_business_unit_code_format")
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200", name=op.f("ck_business_unit_name_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_business_unit_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_unit")),
        sa.UniqueConstraint("organisation_id", "code", name="uq_business_unit_org_code"),
        sa.UniqueConstraint("organisation_id", "id", name="uq_business_unit_org_id"),
    )
    op.create_table(
        "document_file",
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(original_filename) BETWEEN 1 AND 255",
            name=op.f("ck_document_file_original_filename_length"),
        ),
        sa.CheckConstraint(
            "octet_length(sha256) = 32", name=op.f("ck_document_file_sha256_length")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_document_file_size_positive")),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_document_file_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_file")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_document_file_org_id"),
        sa.UniqueConstraint("storage_key", name=op.f("uq_document_file_storage_key")),
    )
    op.create_table(
        "role",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "max_classification",
            _enum("classification"),
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
        sa.CheckConstraint("name ~ '^[a-z][a-z0-9_-]{0,62}$'", name=op.f("ck_role_name_format")),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_role_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_role_org_id"),
        sa.UniqueConstraint("organisation_id", "name", name="uq_role_org_name"),
    )
    op.create_table(
        "source_system",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code ~ '^[a-z][a-z0-9_-]{0,62}$'", name=op.f("ck_source_system_code_format")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_source_system_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_system")),
        sa.UniqueConstraint("organisation_id", "code", name="uq_source_system_org_code"),
        sa.UniqueConstraint("organisation_id", "id", name="uq_source_system_org_id"),
    )
    op.create_table(
        "document",
        sa.Column("business_unit_id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=False),
        sa.Column("source_system_external_id", sa.Text(), nullable=True),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "classification",
            _enum("classification"),
            nullable=False,
        ),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "char_length(canonical_key) BETWEEN 1 AND 255",
            name=op.f("ck_document_canonical_key_length"),
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 500", name=op.f("ck_document_title_length")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_document_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "source_system_id"],
            ["source_system.organisation_id", "source_system.id"],
            name=op.f("fk_document_organisation_id_source_system"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_document_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint(
            "organisation_id", "canonical_key", name="uq_document_org_canonical_key"
        ),
        sa.UniqueConstraint("organisation_id", "id", name="uq_document_org_id"),
        sa.UniqueConstraint(
            "organisation_id",
            "source_system_id",
            "source_system_external_id",
            name="uq_document_org_source_external_id",
        ),
    )
    op.create_index("ix_document_business_unit_id", "document", ["business_unit_id"], unique=False)
    op.create_table(
        "permission_subject",
        sa.Column("kind", _enum("subject_kind"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column("business_unit_id", sa.Uuid(), nullable=True),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind <> 'group' OR (group_name ~ '^[A-Za-z0-9._-]{1,128}$' AND user_id IS NULL AND role_id IS NULL AND business_unit_id IS NULL AND key = 'group:' || group_name)",
            name=op.f("ck_permission_subject_group_shape"),
        ),
        sa.CheckConstraint(
            "kind <> 'role' OR (role_id IS NOT NULL AND business_unit_id IS NOT NULL AND user_id IS NULL AND group_name IS NULL AND key = 'role:' || business_unit_id::text || ':' || role_id::text)",
            name=op.f("ck_permission_subject_role_shape"),
        ),
        sa.CheckConstraint(
            "kind <> 'user' OR (user_id IS NOT NULL AND role_id IS NULL AND business_unit_id IS NULL AND group_name IS NULL AND key = 'user:' || user_id::text)",
            name=op.f("ck_permission_subject_user_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_permission_subject_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "role_id"],
            ["role.organisation_id", "role.id"],
            name=op.f("fk_permission_subject_organisation_id_role"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_permission_subject_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_permission_subject_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_subject")),
        sa.UniqueConstraint("organisation_id", "id", name="uq_permission_subject_org_id"),
        sa.UniqueConstraint("organisation_id", "key", name="uq_permission_subject_org_key"),
    )
    op.create_index(
        "ix_permission_subject_user_id", "permission_subject", ["user_id"], unique=False
    )
    op.create_table(
        "user_role_grant",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("business_unit_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "business_unit_id"],
            ["business_unit.organisation_id", "business_unit.id"],
            name=op.f("fk_user_role_grant_organisation_id_business_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "role_id"],
            ["role.organisation_id", "role.id"],
            name=op.f("fk_user_role_grant_organisation_id_role"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["app_user.organisation_id", "app_user.id"],
            name=op.f("fk_user_role_grant_organisation_id_app_user"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_user_role_grant_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_role_grant")),
        sa.UniqueConstraint(
            "user_id", "role_id", "business_unit_id", name="uq_user_role_grant_user_role_bu"
        ),
    )
    op.create_table(
        "document_version",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_version_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
            "version_number > 0", name=op.f("ck_document_version_version_number_positive")
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_id"],
            ["document.organisation_id", "document.id"],
            name=op.f("fk_document_version_organisation_id_document"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "file_id"],
            ["document_file.organisation_id", "document_file.id"],
            name=op.f("fk_document_version_organisation_id_document_file"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "superseded_by_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name="fk_document_version_superseded_by",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_document_version_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_version")),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_version_document_number"
        ),
        sa.UniqueConstraint("organisation_id", "id", name="uq_document_version_org_id"),
    )
    op.create_table(
        "document_acl",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column(
            "access_level",
            _enum("access_level"),
            nullable=False,
        ),
        sa.Column(
            "source",
            _enum("acl_source"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "document_version_id"],
            ["document_version.organisation_id", "document_version.id"],
            name=op.f("fk_document_acl_organisation_id_document_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "subject_id"],
            ["permission_subject.organisation_id", "permission_subject.id"],
            name=op.f("fk_document_acl_organisation_id_permission_subject"),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisation.id"],
            name=op.f("fk_document_acl_organisation_id_organisation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_acl")),
        sa.UniqueConstraint(
            "document_version_id",
            "subject_id",
            "access_level",
            name="uq_document_acl_version_subject_level",
        ),
    )
    op.create_index("ix_document_acl_subject_id", "document_acl", ["subject_id"], unique=False)

    # document <-> document_version reference each other; add this side once both exist.
    op.create_foreign_key(
        "fk_document_current_version",
        "document",
        "document_version",
        ["organisation_id", "current_version_id"],
        ["organisation_id", "id"],
    )

    _enable_row_level_security()

    # Organisations are provisioned by operators, not by the API.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON organisation FROM {APP_GROUP_ROLE}")


def _enable_row_level_security() -> None:
    # NULL when unset or empty, so an unscoped transaction matches no rows (fail closed).
    op.execute(
        """
        CREATE FUNCTION lekha_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT NULLIF(current_setting('lekha.tenant_id', true), '')::uuid $$
        """
    )
    # Policies apply to the app role. The owner (migrations, operators) bypasses RLS
    # because the tables are not FORCE ROW LEVEL SECURITY.
    op.execute("ALTER TABLE organisation ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON organisation USING (id = lekha_current_tenant())")
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organisation_id = lekha_current_tenant()) "
            "WITH CHECK (organisation_id = lekha_current_tenant())"
        )


def downgrade() -> None:
    op.drop_constraint("fk_document_current_version", "document", type_="foreignkey")
    op.drop_index("ix_document_acl_subject_id", table_name="document_acl")
    op.drop_table("document_acl")
    op.drop_table("document_version")
    op.drop_table("user_role_grant")
    op.drop_index("ix_permission_subject_user_id", table_name="permission_subject")
    op.drop_table("permission_subject")
    op.drop_index("ix_document_business_unit_id", table_name="document")
    op.drop_table("document")
    op.drop_table("source_system")
    op.drop_table("role")
    op.drop_table("document_file")
    op.drop_table("business_unit")
    op.drop_table("app_user")
    op.drop_table("organisation")
    op.execute("DROP FUNCTION lekha_current_tenant()")
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=False)
