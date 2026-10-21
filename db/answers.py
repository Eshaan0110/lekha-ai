"""Writing the question and answer logs. Callers run these inside ``tenant_transaction``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import AnswerLog, Citation, DocumentVersion, QueryLog


async def record_query(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    language: str,
) -> uuid.UUID:
    query_id = uuid.uuid4()
    session.add(
        QueryLog(
            id=query_id,
            organisation_id=organisation_id,
            user_id=user_id,
            question=question,
            language=language,
        )
    )
    return query_id


@dataclass(frozen=True, slots=True)
class CitationRecord:
    source_number: int
    chunk_id: uuid.UUID
    document_version_id: uuid.UUID
    span: str


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    model: str | None
    prompt_sha256: bytes | None
    answer: str | None
    abstention_reason: str | None
    candidate_count: int
    dropped_citations: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    citations: Sequence[CitationRecord]


async def record_answer(
    session: AsyncSession, organisation_id: uuid.UUID, query_id: uuid.UUID, record: AnswerRecord
) -> uuid.UUID:
    answer_id = uuid.uuid4()
    session.add(
        AnswerLog(
            id=answer_id,
            organisation_id=organisation_id,
            query_log_id=query_id,
            model=record.model,
            prompt_sha256=record.prompt_sha256,
            answer=record.answer,
            abstention_reason=record.abstention_reason,
            candidate_count=record.candidate_count,
            dropped_citations=record.dropped_citations,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=record.latency_ms,
        )
    )
    await session.flush()  # the citations reference the answer row
    session.add_all(
        Citation(
            organisation_id=organisation_id,
            answer_log_id=answer_id,
            document_version_id=c.document_version_id,
            chunk_id=c.chunk_id,
            source_number=c.source_number,
            span=c.span,
        )
        for c in record.citations
    )
    return answer_id


async def redact_answers_citing(
    session: AsyncSession, document_id: uuid.UUID, *, now: datetime
) -> int:
    """Deletion workflow: wipe the text of every logged answer that cited the document and
    remove its citations (they quote it). The rows stay so the audit trail resolves."""
    versions = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
    answer_ids = list(
        (
            await session.execute(
                select(Citation.answer_log_id)
                .where(Citation.document_version_id.in_(versions))
                .distinct()
            )
        ).scalars()
    )
    if not answer_ids:
        return 0
    await session.execute(delete(Citation).where(Citation.answer_log_id.in_(answer_ids)))
    await session.execute(
        update(AnswerLog).where(AnswerLog.id.in_(answer_ids)).values(answer=None, redacted_at=now)
    )
    return len(answer_ids)
