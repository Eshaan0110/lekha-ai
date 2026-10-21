"""POST /api/v1/ask: a grounded answer with verified citations, or an abstention.

Flow (plan "Retrieval" steps a-n):
1. Rate limit per user; the question is logged before any retrieval (query_log).
2. Retrieval is permission-filtered (shared with search): the model only ever sees chunks
   the asker may read.
3. Weak or no evidence → abstain without calling the model.
4. The fixed prompt fences sources as untrusted data; the model must reply in JSON.
5. Every citation is verified against its source; unsupported ones are dropped, and if none
   survive Lekha abstains. Abstention is a normal 200 answer (plan error table).
6. The answer and its citations are logged (answer_log, citation). Question and answer text
   go to the database only, never to application logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.ratelimit import RateLimiter, RateLimiterUnavailableError
from apps.api.retrieval import Retrieved, retrieve
from db.answers import AnswerRecord, CitationRecord, record_answer, record_feedback, record_query
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext
from lekha.generation import (
    ANSWER_SCHEMA,
    SYSTEM_PROMPT,
    AbstentionReason,
    Outcome,
    SourceBlock,
    abstain_message,
    evidence_is_sufficient,
    parse_model_output,
    render_user_prompt,
    verify,
)
from lekha.ingestion import Language, detect_language
from model_layer import Embedder, LanguageModel, LanguageModelError, LlmRequest, LlmResponse

logger = structlog.get_logger("lekha.ask")

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1000)


class CitationOut(BaseModel):
    number: int
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    title: str
    heading_path: list[str]
    chunk_id: uuid.UUID
    span: str


class AskResponse(BaseModel):
    query_id: uuid.UUID
    answer: str
    abstained: bool
    abstention_reason: AbstentionReason | None
    citations: list[CitationOut]
    language: str
    model: str | None


Progress = Callable[[str, dict[str, Any]], Awaitable[None]]  # Any: small JSON event payloads


async def _no_progress(stage: str, data: dict[str, Any]) -> None:
    return None


@dataclass(frozen=True, slots=True)
class _Prepared:
    model: LanguageModel
    language: str
    query_id: uuid.UUID
    rate_headers: dict[str, str]
    started: float


async def _prepare(request: Request, ctx: AuthContext, question: str) -> _Prepared:
    """Checks that must pass before anything is streamed, then the query_log row."""
    started = time.monotonic()
    model: LanguageModel | None = request.app.state.language_model
    if model is None:
        raise HTTPException(status_code=503, detail="No answer model is configured.")
    rate_headers = await _check_rate_limit(request, ctx)
    detected = detect_language(question)
    language = Language.ENGLISH.value if detected is Language.UNKNOWN else detected.value
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        query_id = await record_query(
            s,
            organisation_id=ctx.organisation_id,
            user_id=ctx.user_id,
            question=question,
            language=language,
        )
    request.scope.setdefault("state", {})["audit_target"] = ("query_log", str(query_id))
    return _Prepared(model, language, query_id, rate_headers, started)


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
        429: {"description": "Too many questions this minute"},
        503: {"description": "No answer model configured, or it is unavailable"},
    },
)
async def ask(
    request: Request, response: Response, ctx: CurrentUser, body: AskRequest
) -> AskResponse:
    prepared = await _prepare(request, ctx, body.question)
    response.headers.update(prepared.rate_headers)
    try:
        return await _answer(request, ctx, body.question, prepared, _no_progress)
    except LanguageModelError:
        raise HTTPException(status_code=503, detail="The answer model is unavailable.") from None


@router.post(
    "/ask/stream",
    responses={
        200: {"description": "Server-sent events: stage, answer, or error"},
        429: {"description": "Too many questions this minute"},
        503: {"description": "No answer model configured"},
    },
)
async def ask_stream(request: Request, ctx: CurrentUser, body: AskRequest) -> StreamingResponse:
    """Same answer as /ask, with progress events while it is prepared. Only progress is
    streamed: the answer text is sent once, after its citations are verified."""
    prepared = await _prepare(request, ctx, body.question)
    events: asyncio.Queue[str | None] = asyncio.Queue()

    async def progress(stage: str, data: dict[str, Any]) -> None:
        await events.put(_sse("stage", {"stage": stage, **data}))

    async def produce() -> None:
        try:
            result = await _answer(request, ctx, body.question, prepared, progress)
            await events.put(_sse("answer", result.model_dump(mode="json")))
        except LanguageModelError:
            unavailable = {
                "code": "upstream_unavailable",
                "message": "The answer model is unavailable.",
            }
            await events.put(_sse("error", unavailable))
        finally:
            await events.put(None)

    task = asyncio.create_task(produce())

    async def stream() -> AsyncIterator[str]:
        yield _sse("stage", {"stage": "received", "query_id": str(prepared.query_id)})
        while (event := await events.get()) is not None:
            yield event
        await task  # surfaces unexpected errors to the server log

    headers = {**prepared.rate_headers, "Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _answer(
    request: Request, ctx: AuthContext, question: str, prepared: _Prepared, progress: Progress
) -> AskResponse:
    settings = request.app.state.settings
    model, language = prepared.model, prepared.language
    await progress("searching", {})
    found = await retrieve(request, ctx, question, limit=settings.ask_top_k)
    sources = _sources(found)
    await progress("sources", {"count": len(sources)})
    llm_reply: LlmResponse | None = None
    prompt_sha256: bytes | None = None
    embedder: Embedder = request.app.state.embedder
    floor = settings.ask_min_relevance
    min_relevance = embedder.min_relevance if floor is None else floor
    if not sources:
        outcome = Outcome(None, (), AbstentionReason.NO_SOURCES)
    elif not evidence_is_sufficient(found.best_dense_score, min_relevance):
        outcome = Outcome(None, (), AbstentionReason.WEAK_EVIDENCE)
    else:
        user_prompt = render_user_prompt(question, sources, language)
        prompt_sha256 = hashlib.sha256(f"{SYSTEM_PROMPT}\n{user_prompt}".encode()).digest()
        await progress("writing", {})
        try:
            llm_reply = await model.generate(
                LlmRequest(
                    system=SYSTEM_PROMPT,
                    user=user_prompt,
                    max_output_tokens=settings.llm_max_output_tokens,
                    json_schema=ANSWER_SCHEMA,
                )
            )
        except LanguageModelError:
            logger.warning("answer_model_unavailable", model=model.model_name)
            raise
        await progress("checking", {})
        parsed = parse_model_output(llm_reply.text)
        outcome = (
            Outcome(None, (), AbstentionReason.INVALID_MODEL_OUTPUT)
            if parsed is None
            else verify(parsed, sources)
        )

    latency_ms = int((time.monotonic() - prepared.started) * 1000)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        await record_answer(
            s,
            ctx.organisation_id,
            prepared.query_id,
            AnswerRecord(
                model=llm_reply.model if llm_reply else None,
                prompt_sha256=prompt_sha256,
                answer=outcome.answer,
                abstention_reason=outcome.abstention.value if outcome.abstention else None,
                candidate_count=len(sources),
                dropped_citations=outcome.dropped_citations,
                input_tokens=llm_reply.input_tokens if llm_reply else None,
                output_tokens=llm_reply.output_tokens if llm_reply else None,
                latency_ms=latency_ms,
                citations=[
                    CitationRecord(c.source.number, c.source.chunk_id, c.source.version_id, c.span)
                    for c in outcome.citations
                ],
            ),
        )
    logger.info(
        "answered",
        abstention=outcome.abstention.value if outcome.abstention else None,
        sources=len(sources),
        citations=len(outcome.citations),
        dropped_citations=outcome.dropped_citations,
        latency_ms=latency_ms,
    )
    return AskResponse(
        query_id=prepared.query_id,
        answer=outcome.answer or abstain_message(language),
        abstained=outcome.abstention is not None,
        abstention_reason=outcome.abstention,
        citations=[
            CitationOut(
                number=c.source.number,
                document_id=c.source.document_id,
                version_id=c.source.version_id,
                version_number=c.source.version_number,
                title=c.source.title,
                heading_path=list(c.source.heading_path),
                chunk_id=c.source.chunk_id,
                span=c.span,
            )
            for c in outcome.citations
        ],
        language=language,
        model=llm_reply.model if llm_reply else None,
    )


def _sources(found: Retrieved) -> list[SourceBlock]:
    return [
        SourceBlock(
            number=number,
            chunk_id=c.chunk.chunk_id,
            document_id=c.chunk.document_id,
            version_id=c.chunk.version_id,
            version_number=c.chunk.version_number,
            title=c.chunk.title,
            heading_path=c.chunk.heading_path,
            language=c.chunk.language,
            text=c.chunk.text,
        )
        for number, c in enumerate(found.candidates, start=1)
    ]


async def _check_rate_limit(request: Request, ctx: AuthContext) -> dict[str, str]:
    limiter: RateLimiter = request.app.state.ask_limiter
    try:
        decision = await limiter.hit("ask", ctx.user_id)
    except RateLimiterUnavailableError:
        logger.warning("rate_limiter_unavailable")
        raise HTTPException(status_code=503, detail="Please try again shortly.") from None
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many questions; please wait a moment.",
            headers={**headers, "Retry-After": str(decision.retry_after)},
        )
    return headers


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    helpful: bool
    comment: str | None = Field(default=None, max_length=1000)


@router.post(
    "/answers/{query_id}/feedback",
    status_code=204,
    responses={404: {"description": "No such question, or it was asked by someone else"}},
)
async def give_feedback(
    request: Request, ctx: CurrentUser, query_id: uuid.UUID, body: FeedbackIn
) -> Response:
    """Was the answer helpful? Only the person who asked may say; a repeat replaces it."""
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        saved = await record_feedback(
            s,
            organisation_id=ctx.organisation_id,
            user_id=ctx.user_id,
            query_id=query_id,
            helpful=body.helpful,
            comment=body.comment or None,
        )
    if not saved:
        raise HTTPException(status_code=404, detail="Not found.")
    request.scope.setdefault("state", {})["audit_target"] = ("query_log", str(query_id))
    return Response(status_code=204)
