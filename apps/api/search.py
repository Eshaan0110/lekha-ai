"""Permission-filtered search and ingestion status.

POST /api/v1/search applies permissions twice: inside the vector index (the payload
filter, built only from the caller's AuthContext and refused if missing), and again against
the database access rule before anything is returned. The query text is never logged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.retrieval import retrieve
from db.ingestion import readable_chunks, visible_job
from db.schema import IngestionStatus
from db.tenancy import tenant_transaction
from model_layer import Embedder

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)

SNIPPET_CHARS = 300


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
    kind: str = "document"  # "document" or "meeting"
    start_ms: int | None = None  # meetings: where the passage starts


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
    found = await retrieve(request, ctx, body.query, limit=body.limit)
    results = [
        SearchResult(
            chunk_id=c.chunk.chunk_id,
            document_id=c.chunk.document_id,
            version_id=c.chunk.version_id,
            title=c.chunk.title,
            snippet=c.chunk.text[:SNIPPET_CHARS],
            language=c.chunk.language,
            heading_path=list(c.chunk.heading_path),
            score=c.score,
            kind=c.chunk.kind,
            start_ms=c.chunk.start_ms,
        )
        for c in found.candidates
    ]
    return SearchResponse(results=results, embedding_model=embedder.model_name)


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


class ChunkOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    title: str
    heading_path: list[str]
    language: str
    text: str
    kind: str
    start_ms: int | None


@router.get(
    "/chunks/{chunk_id}",
    response_model=ChunkOut,
    responses={404: {"description": "Not found, not current, or not visible to the caller"}},
)
async def get_chunk(request: Request, ctx: CurrentUser, chunk_id: uuid.UUID) -> ChunkOut:
    """The full source passage behind a citation, under the same access rule as search."""
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        found = await readable_chunks(s, ctx, [chunk_id], now=datetime.now(UTC))
    chunk = found.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return ChunkOut(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        version_number=chunk.version_number,
        title=chunk.title,
        heading_path=list(chunk.heading_path),
        language=chunk.language,
        text=chunk.text,
        kind=chunk.kind,
        start_ms=chunk.start_ms,
    )
