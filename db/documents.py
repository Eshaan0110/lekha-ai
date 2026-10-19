"""Writing newly uploaded documents. Callers run these inside ``tenant_transaction``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import (
    Document,
    DocumentAcl,
    DocumentFile,
    DocumentVersion,
    PermissionSubject,
    SourceSystem,
)
from lekha.auth import AccessLevel, AclSource, Classification
from lekha.memory import ValidatedFile

UPLOAD_SOURCE_CODE = "upload"


@dataclass(frozen=True, slots=True)
class NewDocument:
    document_id: uuid.UUID
    version_id: uuid.UUID
    file_id: uuid.UUID
    organisation_id: uuid.UUID
    business_unit_id: uuid.UUID
    title: str
    canonical_key: str
    classification: Classification
    storage_key: str
    file: ValidatedFile
    subject_ids: Sequence[uuid.UUID]


async def resolve_subject_ids(session: AsyncSession, keys: Sequence[str]) -> dict[str, uuid.UUID]:
    """Map badge keys to permission_subject IDs (within the session's tenant)."""
    rows = await session.execute(
        select(PermissionSubject.key, PermissionSubject.id).where(PermissionSubject.key.in_(keys))
    )
    return {key: subject_id for key, subject_id in rows}


async def canonical_key_taken(session: AsyncSession, canonical_key: str) -> bool:
    found = await session.scalar(
        select(Document.id).where(Document.canonical_key == canonical_key).limit(1)
    )
    return found is not None


async def upload_source_id(session: AsyncSession) -> uuid.UUID | None:
    result: uuid.UUID | None = await session.scalar(
        select(SourceSystem.id).where(SourceSystem.code == UPLOAD_SOURCE_CODE)
    )
    return result


async def insert_uploaded_document(
    session: AsyncSession, new: NewDocument, *, source_system_id: uuid.UUID, now: datetime
) -> None:
    """Insert document, file, version 1 and its guest list, then mark version 1 current."""
    org = new.organisation_id
    session.add(
        Document(
            id=new.document_id,
            organisation_id=org,
            business_unit_id=new.business_unit_id,
            source_system_id=source_system_id,
            canonical_key=new.canonical_key,
            title=new.title,
            classification=new.classification,
        )
    )
    session.add(
        DocumentFile(
            id=new.file_id,
            organisation_id=org,
            storage_key=new.storage_key,
            sha256=new.file.sha256,
            size_bytes=new.file.size_bytes,
            media_type=new.file.media_type,
            original_filename=new.file.filename,
        )
    )
    # document and document_version reference each other: write in explicit layers.
    await session.flush()
    session.add(
        DocumentVersion(
            id=new.version_id,
            organisation_id=org,
            document_id=new.document_id,
            version_number=1,
            file_id=new.file_id,
            published_at=now,
        )
    )
    await session.flush()
    session.add_all(
        DocumentAcl(
            organisation_id=org,
            document_version_id=new.version_id,
            subject_id=subject_id,
            access_level=AccessLevel.READ,
            source=AclSource.EXPLICIT,
        )
        for subject_id in new.subject_ids
    )
    await session.execute(
        update(Document)
        .where(Document.id == new.document_id)
        .values(current_version_id=new.version_id)
    )
