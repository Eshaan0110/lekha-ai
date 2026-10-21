"""Retrieval shared by search and ask: permission-filtered meaning search, optional
permission-filtered keyword search, rank fusion, then the database re-check.

Permissions are applied in every path before any text is returned: the vector filter is
built from the caller's AuthContext (refused if empty), keyword search runs the access rule
in SQL, and every candidate is re-checked against the database before use.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request

from db.ingestion import SearchableChunk, readable_chunks
from db.search import keyword_search
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext
from lekha.retrieval import (
    NoSearchableScopeError,
    QdrantIndex,
    build_permission_filter,
    reciprocal_rank_fusion,
)
from model_layer import Embedder

OVERFETCH = 3  # fetch extra so the re-check can drop some and still fill the page


@dataclass(frozen=True, slots=True)
class Candidate:
    chunk: SearchableChunk
    score: float  # fused rank score (hybrid) or cosine similarity (dense)
    dense_score: float | None  # cosine similarity, when meaning search found it


@dataclass(frozen=True, slots=True)
class Retrieved:
    candidates: list[Candidate]
    best_dense_score: float | None


async def retrieve(request: Request, ctx: AuthContext, query: str, *, limit: int) -> Retrieved:
    settings = request.app.state.settings
    embedder: Embedder = request.app.state.embedder
    try:
        query_filter = build_permission_filter(ctx)
    except NoSearchableScopeError:
        return Retrieved([], None)
    [vector] = await asyncio.to_thread(embedder.embed, [query])
    index: QdrantIndex = request.app.state.vector_index
    hits = await index.search(vector, query_filter, limit=limit * OVERFETCH)
    dense = {hit.chunk_id: hit.score for hit in hits}

    now = datetime.now(UTC)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        keyword: list[tuple[uuid.UUID, float]] = []
        if settings.retrieval_mode == "hybrid":
            keyword = await keyword_search(s, ctx, query, limit=limit * OVERFETCH, now=now)
        if keyword:
            ranked = reciprocal_rank_fusion([h.chunk_id for h in hits], [c for c, _ in keyword])
        else:
            ranked = [(hit.chunk_id, hit.score) for hit in hits]
        allowed = await readable_chunks(s, ctx, [chunk_id for chunk_id, _ in ranked], now=now)

    candidates = [
        Candidate(chunk, score, dense.get(chunk_id))
        for chunk_id, score in ranked
        if (chunk := allowed.get(chunk_id)) is not None
    ][:limit]
    scores = [c.dense_score for c in candidates if c.dense_score is not None]
    return Retrieved(candidates, max(scores) if scores else None)
