"""Persistence for ingestion jobs and chunks. Callers run these inside ``tenant_transaction``."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.access import readable_condition
from db.schema import (
    ChunkRecord,
    Document,
    DocumentAcl,
    DocumentFile,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    PermissionSubject,
)
from lekha.auth import CURATOR_ROLE, AccessLevel, AuthContext, Classification
from lekha.ingestion import Chunk
from lekha.memory import MEDIA_TYPES, FileKind

_KIND_BY_MEDIA_TYPE = {media: kind for kind, media in MEDIA_TYPES.items()}


@dataclass(frozen=True, slots=True)
class VersionToIngest:
    job_id: uuid.UUID
    organisation_id: uuid.UUID
    business_unit_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    classification: Classification
    storage_key: str
    kind: FileKind
    allowed_subjects: tuple[str, ...]  # badge keys with an unexpired read entry, right now
    is_current: bool
    deleted: bool


async def create_job(
    session: AsyncSession, organisation_id: uuid.UUID, version_id: uuid.UUID
) -> uuid.UUID:
    job_id = uuid.uuid4()
    await session.execute(
        insert(IngestionJob).values(
            id=job_id,
            organisation_id=organisation_id,
            document_version_id=version_id,
            status=IngestionStatus.QUEUED,
        )
    )
    return job_id


async def load_version_to_ingest(
    session: AsyncSession, job_id: uuid.UUID, *, now: datetime
) -> VersionToIngest | None:
    row = (
        await session.execute(
            select(
                IngestionJob.organisation_id,
                Document.business_unit_id,
                Document.id,
                DocumentVersion.id,
                Document.classification,
                DocumentFile.storage_key,
                DocumentFile.media_type,
                Document.current_version_id,
                Document.deleted_at,
            )
            .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(DocumentFile, DocumentFile.id == DocumentVersion.file_id)
            .where(IngestionJob.id == job_id)
        )
    ).first()
    if row is None:
        return None
    org, bu, doc_id, version_id, classification, key, media_type, current, deleted_at = row
    subjects = (
        await session.execute(
            select(PermissionSubject.key)
            .join(DocumentAcl, DocumentAcl.subject_id == PermissionSubject.id)
            .where(
                DocumentAcl.document_version_id == version_id,
                DocumentAcl.access_level == AccessLevel.READ,
                or_(DocumentAcl.expires_at.is_(None), DocumentAcl.expires_at > now),
            )
        )
    ).scalars()
    return VersionToIngest(
        job_id=job_id,
        organisation_id=org,
        business_unit_id=bu,
        document_id=doc_id,
        version_id=version_id,
        classification=classification,
        storage_key=key,
        kind=_KIND_BY_MEDIA_TYPE[media_type],
        allowed_subjects=tuple(sorted(set(subjects))),
        is_current=current == version_id,
        deleted=deleted_at is not None,
    )


async def update_job(session: AsyncSession, job_id: uuid.UUID, **values: Any) -> None:
    await session.execute(update(IngestionJob).where(IngestionJob.id == job_id).values(**values))


async def replace_chunks(
    session: AsyncSession,
    target: VersionToIngest,
    chunks: Sequence[Chunk],
    point_ids: Sequence[uuid.UUID],
    *,
    embedding_model: str,
) -> None:
    """Delete any earlier chunks of the version (a retry), then insert these."""
    await session.execute(
        delete(ChunkRecord).where(ChunkRecord.document_version_id == target.version_id)
    )
    if chunks:
        await session.execute(
            insert(ChunkRecord),
            [
                {
                    "id": point_id,
                    "organisation_id": target.organisation_id,
                    "document_version_id": target.version_id,
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "text_sha256": hashlib.sha256(chunk.text.encode()).digest(),
                    "token_count": chunk.token_count,
                    "page": chunk.page,
                    "heading_path": list(chunk.heading_path),
                    "language": chunk.language.value,
                    "embedding_model": embedding_model,
                }
                for chunk, point_id in zip(chunks, point_ids, strict=True)
            ],
        )


@dataclass(frozen=True, slots=True)
class SearchableChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    title: str
    text: str
    language: str
    heading_path: tuple[str, ...]


async def readable_chunks(
    session: AsyncSession, ctx: AuthContext, chunk_ids: Sequence[uuid.UUID], *, now: datetime
) -> dict[uuid.UUID, SearchableChunk]:
    """Second safety net after the vector filter: re-check the hits against the database
    access rule (catches expired shares, revoked roles, or deletions not yet indexed)."""
    if not chunk_ids:
        return {}
    rows = await session.execute(
        select(
            ChunkRecord.id,
            Document.id,
            DocumentVersion.id,
            DocumentVersion.version_number,
            Document.title,
            ChunkRecord.text,
            ChunkRecord.language,
            ChunkRecord.heading_path,
        )
        .join(DocumentVersion, DocumentVersion.id == ChunkRecord.document_version_id)
        .join(
            Document,
            and_(
                Document.id == DocumentVersion.document_id,
                Document.current_version_id == DocumentVersion.id,
            ),
        )
        .where(ChunkRecord.id.in_(chunk_ids), readable_condition(ctx, now=now))
    )
    return {
        r[0]: SearchableChunk(r[0], r[1], r[2], r[3], r[4], r[5], r[6], tuple(r[7])) for r in rows
    }


async def visible_job(
    session: AsyncSession, ctx: AuthContext, job_id: uuid.UUID, *, now: datetime
) -> IngestionJob | None:
    """A job is visible to curators of its business unit, or to anyone who may read the
    document. Otherwise None (reported as 404, like an invisible document)."""
    curator_units = [bu for bu, roles in ctx.roles_by_bu.items() if CURATOR_ROLE in roles]
    allowed = readable_condition(ctx, now=now)
    if curator_units:
        allowed = or_(allowed, Document.business_unit_id.in_(curator_units))
    job: IngestionJob | None = (
        await session.execute(
            select(IngestionJob)
            .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(IngestionJob.id == job_id, allowed)
        )
    ).scalar_one_or_none()
    return job


@dataclass(frozen=True, slots=True)
class PublishResult:
    current_version_id: uuid.UUID | None  # None: the document was deleted meanwhile
    published: bool  # did this version become (or stay) current?


async def publish_version(
    session: AsyncSession, target: VersionToIngest, *, now: datetime
) -> PublishResult:
    """Plan stage 13: make an indexed version current, superseding the previous one.

    The document row is locked, so two versions finishing together are ordered. A version
    older than the current one (it finished late) is recorded as superseded instead, and
    nothing is published for a document deleted while its version was being processed.
    """
    doc = (
        await session.execute(
            select(Document.current_version_id, Document.deleted_at)
            .where(Document.id == target.document_id)
            .with_for_update()
        )
    ).one()
    if doc.deleted_at is not None:
        return PublishResult(current_version_id=None, published=False)
    if doc.current_version_id == target.version_id:
        return PublishResult(current_version_id=target.version_id, published=True)

    rows = await session.execute(
        select(DocumentVersion.id, DocumentVersion.version_number).where(
            DocumentVersion.id.in_([target.version_id, doc.current_version_id])
        )
    )
    numbers = {version_id: number for version_id, number in rows}
    current = doc.current_version_id
    if current is not None and numbers[current] > numbers[target.version_id]:
        await session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == target.version_id)
            .values(superseded_at=now, superseded_by_version_id=current)
        )
        return PublishResult(current_version_id=current, published=False)

    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == target.version_id)
        .values(published_at=now)
    )
    if current is not None:
        await session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == current)
            .values(superseded_at=now, superseded_by_version_id=target.version_id)
        )
    await session.execute(
        update(Document)
        .where(Document.id == target.document_id)
        .values(current_version_id=target.version_id)
    )
    return PublishResult(current_version_id=target.version_id, published=True)
