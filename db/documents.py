"""Writing uploaded documents and new versions. Callers run these inside ``tenant_transaction``."""

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


@dataclass(frozen=True, slots=True)
class ExistingDocument:
    document_id: uuid.UUID
    business_unit_id: uuid.UUID
    classification: Classification
    deleted: bool
    next_version_number: int
    latest_sha256: bytes  # file checksum of the highest-numbered version


async def lock_document_by_key(
    session: AsyncSession, canonical_key: str
) -> ExistingDocument | None:
    """The document with this canonical key, locked until the transaction ends so two new
    versions of it can't be numbered at the same time; None if the key is new."""
    row = (
        await session.execute(
            select(
                Document.id,
                Document.business_unit_id,
                Document.classification,
                Document.deleted_at,
            )
            .where(Document.canonical_key == canonical_key)
            .with_for_update()
        )
    ).first()
    if row is None:
        return None
    latest = (
        await session.execute(
            select(DocumentVersion.version_number, DocumentFile.sha256)
            .join(DocumentFile, DocumentFile.id == DocumentVersion.file_id)
            .where(DocumentVersion.document_id == row.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
    ).one()
    return ExistingDocument(
        document_id=row.id,
        business_unit_id=row.business_unit_id,
        classification=row.classification,
        deleted=row.deleted_at is not None,
        next_version_number=latest.version_number + 1,
        latest_sha256=latest.sha256,
    )


async def upload_source_id(session: AsyncSession) -> uuid.UUID | None:
    result: uuid.UUID | None = await session.scalar(
        select(SourceSystem.id).where(SourceSystem.code == UPLOAD_SOURCE_CODE)
    )
    return result


async def insert_uploaded_document(
    session: AsyncSession, new: NewDocument, *, source_system_id: uuid.UUID, now: datetime
) -> None:
    """Insert document, file, version 1 and its guest list, then mark version 1 current."""
    session.add(
        Document(
            id=new.document_id,
            organisation_id=new.organisation_id,
            business_unit_id=new.business_unit_id,
            source_system_id=source_system_id,
            canonical_key=new.canonical_key,
            title=new.title,
            classification=new.classification,
        )
    )
    await _insert_version(session, new, version_number=1, published_at=now)
    await session.execute(
        update(Document)
        .where(Document.id == new.document_id)
        .values(current_version_id=new.version_id)
    )


async def insert_new_version(
    session: AsyncSession, new: NewDocument, *, version_number: int
) -> None:
    """Add a version to an existing document. It is not published: readers keep the current
    version until the new one is indexed (plan: "publish" stage)."""
    await _insert_version(session, new, version_number=version_number, published_at=None)
    await session.execute(
        update(Document).where(Document.id == new.document_id).values(title=new.title)
    )


async def _insert_version(
    session: AsyncSession, new: NewDocument, *, version_number: int, published_at: datetime | None
) -> None:
    org = new.organisation_id
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
            version_number=version_number,
            file_id=new.file_id,
            published_at=published_at,
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
