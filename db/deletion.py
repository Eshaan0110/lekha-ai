"""Persistence for the document deletion workflow (plan: "Retention and deletion workflows").

Step 1 (API, one transaction): record the request and soft-delete the document and its
versions. From that commit on, the access rule hides it everywhere.
Step 2 (worker): purge derived data (vectors, chunk rows, stored files), then resolve the
request with counts. Callers run these inside ``tenant_transaction``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.access import readable_condition
from db.schema import (
    ChunkRecord,
    DeletionRequest,
    DeletionStatus,
    Document,
    DocumentFile,
    DocumentVersion,
)
from lekha.auth import AuthContext, Classification


@dataclass(frozen=True, slots=True)
class LiveDocument:
    document_id: uuid.UUID
    business_unit_id: uuid.UUID
    classification: Classification


async def lock_live_document(session: AsyncSession, document_id: uuid.UUID) -> LiveDocument | None:
    row = (
        await session.execute(
            select(Document.id, Document.business_unit_id, Document.classification)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
            .with_for_update()
        )
    ).first()
    return LiveDocument(row[0], row[1], row[2]) if row is not None else None


async def caller_can_read(
    session: AsyncSession, ctx: AuthContext, document_id: uuid.UUID, *, now: datetime
) -> bool:
    found = await session.scalar(
        select(Document.id)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(Document.id == document_id, readable_condition(ctx, now=now))
    )
    return found is not None


async def soft_delete_document(
    session: AsyncSession, ctx: AuthContext, document_id: uuid.UUID, *, now: datetime
) -> uuid.UUID:
    """Record the request and hide the document and every version. Returns the request ID."""
    request_id = uuid.uuid4()
    session.add(
        DeletionRequest(
            id=request_id,
            organisation_id=ctx.organisation_id,
            scope="document",
            document_id=document_id,
            requested_by=ctx.user_id,
            status=DeletionStatus.PENDING,
        )
    )
    await session.execute(update(Document).where(Document.id == document_id).values(deleted_at=now))
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.document_id == document_id, DocumentVersion.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    return request_id


@dataclass(frozen=True, slots=True)
class PurgeTarget:
    request_id: uuid.UUID
    document_id: uuid.UUID
    storage_keys: tuple[str, ...]
    status: DeletionStatus


async def load_purge_target(session: AsyncSession, request_id: uuid.UUID) -> PurgeTarget | None:
    request = await session.get(DeletionRequest, request_id)
    if request is None:
        return None
    keys = (
        await session.execute(
            select(DocumentFile.storage_key)
            .join(DocumentVersion, DocumentVersion.file_id == DocumentFile.id)
            .where(DocumentVersion.document_id == request.document_id)
        )
    ).scalars()
    return PurgeTarget(request.id, request.document_id, tuple(sorted(keys)), request.status)


async def delete_document_chunks(session: AsyncSession, document_id: uuid.UUID) -> int:
    versions = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
    count = await session.scalar(
        select(func.count())
        .select_from(ChunkRecord)
        .where(ChunkRecord.document_version_id.in_(versions))
    )
    await session.execute(delete(ChunkRecord).where(ChunkRecord.document_version_id.in_(versions)))
    return count or 0


async def resolve_request(
    session: AsyncSession,
    request_id: uuid.UUID,
    *,
    status: DeletionStatus,
    now: datetime,
    chunks_removed: int | None = None,
    files_removed: int | None = None,
    error_code: str | None = None,
) -> None:
    await session.execute(
        update(DeletionRequest)
        .where(DeletionRequest.id == request_id)
        .values(
            status=status,
            completed_at=now if status is DeletionStatus.COMPLETED else None,
            chunks_removed=chunks_removed,
            files_removed=files_removed,
            error_code=error_code,
        )
    )


async def visible_deletion_request(
    session: AsyncSession, ctx: AuthContext, request_id: uuid.UUID, curator_units: list[uuid.UUID]
) -> DeletionRequest | None:
    """Visible to whoever asked for it and to curators of the document's business unit."""
    request: DeletionRequest | None = (
        await session.execute(
            select(DeletionRequest)
            .join(Document, Document.id == DeletionRequest.document_id)
            .where(
                DeletionRequest.id == request_id,
                (DeletionRequest.requested_by == ctx.user_id)
                | Document.business_unit_id.in_(curator_units),
            )
        )
    ).scalar_one_or_none()
    return request
