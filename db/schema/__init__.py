"""SQLAlchemy ORM mappings. The only package that defines persistence mappings."""

from db.schema.answers import AnswerFeedback, AnswerLog, Citation, QueryLog
from db.schema.audit import AuditEventRecord
from db.schema.deletion import DeletionRequest, DeletionStatus
from db.schema.documents import (
    Document,
    DocumentAcl,
    DocumentFile,
    DocumentVersion,
    SourceSystem,
)
from db.schema.glossary import GlossaryEntry, GlossaryName
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
from db.schema.meetings import Meeting, MeetingMinutes, MeetingParticipant, MinutesStatus

__all__ = [
    "AnswerFeedback",
    "AnswerLog",
    "AppUser",
    "AuditEventRecord",
    "BusinessUnit",
    "ChunkRecord",
    "Citation",
    "DeletionRequest",
    "DeletionStatus",
    "Document",
    "DocumentAcl",
    "DocumentFile",
    "DocumentVersion",
    "GlossaryEntry",
    "GlossaryName",
    "IdempotencyRecord",
    "IdempotencyState",
    "IngestionJob",
    "IngestionStatus",
    "Meeting",
    "MeetingMinutes",
    "MeetingParticipant",
    "MinutesStatus",
    "Organisation",
    "PermissionSubject",
    "QueryLog",
    "Role",
    "SourceSystem",
    "UserRoleGrant",
]
