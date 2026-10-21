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

from db.glossary import visible_entries
from db.ingestion import SearchableChunk, readable_chunks
from db.search import keyword_search
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext
from lekha.memory import Expansion, expand_query, find_expansions
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
    expansions: tuple[Expansion, ...] = ()  # glossary entries the question mentioned

    @property
    def also_searched(self) -> list[str]:
        """The other names searched for, as shown to the user."""
        return [n for e in self.expansions for n in e.entry.names if n != e.matched]


async def retrieve(request: Request, ctx: AuthContext, query: str, *, limit: int) -> Retrieved:
    settings = request.app.state.settings
    embedder: Embedder = request.app.state.embedder
    try:
        query_filter = build_permission_filter(ctx)
    except NoSearchableScopeError:
        return Retrieved([], None)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        glossary = [stored.entry for stored in await visible_entries(s, ctx)]
    expansions = tuple(find_expansions(query, glossary))
    query = expand_query(query, expansions)
    [vector] = await asyncio.to_thread(embedder.embed, [query])
    index: QdrantIndex = request.app.state.vector_index
    hits = await index.search(vector, query_filter, limit=limit * OVERFETCH)
    dense = {hit.chunk_id: hit.score for hit in hits}

    now = datetime.now(UTC)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        keyword: list[tuple[uuid.UUID, float]] = []
        named: list[tuple[uuid.UUID, float]] = []
        if settings.retrieval_mode == "hybrid":
            keyword = await keyword_search(s, ctx, query, limit=limit * OVERFETCH, now=now)
            if expansions:
                # A glossary name is a strong signal: rank passages that use any name of a
                # mentioned entry as their own list, so generic words can't drown them out.
                names = " ".join(n for e in expansions for n in e.entry.names)
                named = await keyword_search(s, ctx, names, limit=limit * OVERFETCH, now=now)
        lists = [[c for c, _ in found] for found in (keyword, named) if found]
        if lists:
            ranked = reciprocal_rank_fusion([h.chunk_id for h in hits], *lists)
        else:
            ranked = [(hit.chunk_id, hit.score) for hit in hits]
        allowed = await readable_chunks(s, ctx, [chunk_id for chunk_id, _ in ranked], now=now)

    candidates = [
        Candidate(chunk, score, dense.get(chunk_id))
        for chunk_id, score in ranked
        if (chunk := allowed.get(chunk_id)) is not None
    ][:limit]
    scores = [c.dense_score for c in candidates if c.dense_score is not None]
    return Retrieved(candidates, max(scores) if scores else None, expansions)
