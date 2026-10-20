"""SQLAlchemy ORM mappings. The only package that defines persistence mappings."""

from db.schema.audit import AuditEventRecord
from db.schema.documents import (
    Document,
    DocumentAcl,
    DocumentFile,
    DocumentVersion,
    SourceSystem,
)
from db.schema.idempotency import IdempotencyRecord, IdempotencyState
from db.schema.identity import (
    AppUser,
    BusinessUnit,
    Organisation,
    PermissionSubject,
    Role,
    UserRoleGrant,
)
from db.schema.ingestion import ChunkRecord, IngestionJob, IngestionStatus

__all__ = [
    "AppUser",
    "AuditEventRecord",
    "BusinessUnit",
    "ChunkRecord",
    "Document",
    "DocumentAcl",
    "DocumentFile",
    "DocumentVersion",
    "IdempotencyRecord",
    "IdempotencyState",
    "IngestionJob",
    "IngestionStatus",
    "Organisation",
    "PermissionSubject",
    "Role",
    "SourceSystem",
    "UserRoleGrant",
]
