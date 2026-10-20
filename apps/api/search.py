"""Permission-filtered search and ingestion status.

POST /api/v1/search applies permissions twice: inside the vector index (the payload
filter, built only from the caller's AuthContext and refused if missing), and again against
the database access rule before anything is returned. The query text is never logged.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import CurrentUser, require_auth_context
from db.ingestion import readable_chunks, visible_job
from db.schema import IngestionStatus
from db.tenancy import tenant_transaction
from lekha.retrieval import NoSearchableScopeError, QdrantIndex, build_permission_filter
from model_layer import Embedder

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)

SNIPPET_CHARS = 300
OVERFETCH = 3  # fetch extra hits so the database re-check can drop some and still fill a page


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    title: str
    snippet: str
    language: str
    heading_path: list[str]
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]
    embedding_model: str


class IngestionJobOut(BaseModel):
    id: uuid.UUID
    document_version_id: uuid.UUID
    status: IngestionStatus
    stage: str
    attempts: int
    error_code: str | None
    chunk_count: int | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


@router.post("/search", response_model=SearchResponse)
async def search(request: Request, ctx: CurrentUser, body: SearchRequest) -> SearchResponse:
    embedder: Embedder = request.app.state.embedder
    try:
        query_filter = build_permission_filter(ctx)
    except NoSearchableScopeError:
        return SearchResponse(results=[], embedding_model=embedder.model_name)
    [vector] = await asyncio.to_thread(embedder.embed, [body.query])
    index: QdrantIndex = request.app.state.vector_index
    hits = await index.search(vector, query_filter, limit=body.limit * OVERFETCH)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        allowed = await readable_chunks(
            s, ctx, [hit.chunk_id for hit in hits], now=datetime.now(UTC)
        )
    results = [
        SearchResult(
            chunk_id=hit.chunk_id,
            document_id=chunk.document_id,
            version_id=chunk.version_id,
            title=chunk.title,
            snippet=chunk.text[:SNIPPET_CHARS],
            language=chunk.language,
            heading_path=list(chunk.heading_path),
            score=hit.score,
        )
        for hit in hits
        if (chunk := allowed.get(hit.chunk_id)) is not None
    ]
    return SearchResponse(results=results[: body.limit], embedding_model=embedder.model_name)


@router.get(
    "/ingestion-jobs/{job_id}",
    response_model=IngestionJobOut,
    responses={404: {"description": "Not found or not visible to the caller"}},
)
async def get_ingestion_job(
    request: Request, ctx: CurrentUser, job_id: uuid.UUID
) -> IngestionJobOut:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        job = await visible_job(s, ctx, job_id, now=datetime.now(UTC))
        if job is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return IngestionJobOut.model_validate(job, from_attributes=True)
