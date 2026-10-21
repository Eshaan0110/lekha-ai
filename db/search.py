"""Keyword search over chunks (the "sparse" half of hybrid retrieval, plan ADR-008).

PostgreSQL full-text search with the 'simple' configuration. The question is split into
words by PostgreSQL itself (the same way the chunks were), and any word may match, ranked by
``ts_rank_cd``. The permission rule is the same SQL as everywhere else
(``db.access.readable_condition``), applied inside this query, so keyword search can never
return a chunk the caller may not read. Callers run this inside ``tenant_transaction``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.access import readable_condition
from db.schema import ChunkRecord, Document, DocumentVersion
from lekha.auth import AuthContext

MAX_QUERY_WORDS = 32


def _or_query(words: list[str]) -> str:
    """'a' | 'b' | 'c' with each word quoted, so no word is read as tsquery syntax."""
    quoted = ("'" + w.replace("\\", "\\\\").replace("'", "''") + "'" for w in words)
    return " | ".join(quoted)


async def query_words(session: AsyncSession, query: str) -> list[str]:
    words = await session.scalar(select(func.tsvector_to_array(func.to_tsvector("simple", query))))
    return sorted(words or [])[:MAX_QUERY_WORDS]


async def keyword_search(
    session: AsyncSession, ctx: AuthContext, query: str, *, limit: int, now: datetime
) -> list[tuple[uuid.UUID, float]]:
    """Readable chunks of current versions matching any word, best first."""
    words = await query_words(session, query)
    if not words:
        return []
    tsquery = func.to_tsquery("simple", _or_query(words))
    rank = func.ts_rank_cd(ChunkRecord.search_vector, tsquery).label("rank")
    rows = await session.execute(
        select(ChunkRecord.id, rank)
        .join(DocumentVersion, DocumentVersion.id == ChunkRecord.document_version_id)
        .join(
            Document,
            and_(
                Document.id == DocumentVersion.document_id,
                Document.current_version_id == DocumentVersion.id,
            ),
        )
        .where(ChunkRecord.search_vector.op("@@")(tsquery), readable_condition(ctx, now=now))
        .order_by(rank.desc(), ChunkRecord.id)
        .limit(limit)
    )
    return [(chunk_id, float(score)) for chunk_id, score in rows]
