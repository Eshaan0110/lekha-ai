"""Meeting memory: POST/GET /api/v1/meetings and GET /api/v1/meetings/{id}.

A meeting is imported from a transcript file (.vtt as exported by Teams, .srt, or .txt).
It is stored as a document of kind "transcript", so search, answers, versions, deletion and
audit treat it like any document, with one difference that matters: **its guest list is
the participants**. Not the business unit, not its curators: a meeting imported from one
person's workspace must not become readable by everyone (plan, "Imports inherit
permissions"). The importer is always a participant.

Importing the same meeting again (same source ID, or same organiser within two minutes of
the same start) adds a new version of that meeting instead of a duplicate.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.ratelimit import RateLimiter, RateLimiterUnavailableError
from apps.api.uploads import WrittenDocument, create_document, enqueue_ingestion
from apps.wiring import JobQueue
from db.meetings import (
    MeetingDetail,
    approve_minutes,
    existing_user_ids,
    find_same_meeting,
    ingestion_job_of,
    list_readable_meetings,
    lock_minutes,
    minutes_of,
    readable_meeting,
    record_meeting,
    record_minutes,
    reject_minutes,
    set_participants,
)
from db.schema import MeetingMinutes, MinutesStatus
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext, Classification, meeting_import_denial, user_badge
from lekha.generation import (
    MINUTES_SCHEMA,
    MINUTES_SYSTEM_PROMPT,
    MinutesTemplate,
    parse_minutes,
    render_minutes_prompt,
    verify_minutes,
)
from lekha.ingestion import (
    Segment,
    TranscriptError,
    format_time,
    from_canonical_json,
    moment_of_quote,
    parse_transcript,
    to_canonical_json,
)
from lekha.memory import (
    MEDIA_TYPES,
    FileKind,
    ObjectStore,
    ObjectStoreError,
    ValidatedFile,
    sanitise_filename,
)
from model_layer import LanguageModel, LanguageModelError, LlmRequest

logger = structlog.get_logger("lekha.meetings")

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)

MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
MAX_PARTICIPANTS = 100
_DENIAL_MESSAGE = {
    "release_gate": "Confidential meetings cannot be imported until the release gate is passed.",
    "not_curator": "You need a role in this business unit to import its meetings.",
    "above_clearance": "You may not import meetings at this classification.",
}


class MeetingMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    business_unit_id: uuid.UUID
    started_at: datetime
    participant_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_PARTICIPANTS)
    classification: Classification = Classification.INTERNAL
    external_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:/-]{1,200}$")

    @field_validator("started_at")
    @classmethod
    def _needs_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("started_at must include a timezone, e.g. 2026-10-14T10:00:00+05:30")
        return value.astimezone(UTC)


class _Spec:
    """The DocumentSpec for a meeting transcript."""

    def __init__(self, metadata: MeetingMetadata, guests: list[str]) -> None:
        self.title = metadata.title
        self.canonical_key = ""  # chosen inside the transaction (re-import detection)
        self.business_unit_id = metadata.business_unit_id
        self.classification = metadata.classification
        self.acl_subjects = guests


class MeetingImported(BaseModel):
    meeting_id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    ingestion_job_id: uuid.UUID
    turns: int
    speakers: list[str]


class MeetingOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    title: str
    started_at: datetime
    version_number: int
    participants: list[str]


class TurnOut(BaseModel):
    at: str | None
    start_ms: int | None
    speaker: str | None
    text: str


class MeetingDetailOut(MeetingOut):
    transcript: list[TurnOut]
    you_organised: bool  # the organiser approves minutes and may delete the meeting


def _metadata(raw: object) -> MeetingMetadata:
    if not isinstance(raw, str) or len(raw) > MAX_METADATA_BYTES:
        raise HTTPException(status_code=422, detail="A 'metadata' JSON field is required.")
    try:
        return MeetingMetadata.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("metadata", *e["loc"])} for e in exc.errors()]
        ) from None


@router.post(
    "/meetings",
    status_code=201,
    response_model=MeetingImported,
    responses={
        403: {"description": "No role in this unit, or blocked by the release gate"},
        409: {"description": "Identical to the latest import, or the meeting was deleted"},
        413: {"description": "Transcript too large"},
        415: {"description": "Not a .vtt, .srt or .txt transcript"},
        422: {"description": "Unreadable transcript, bad metadata, or unknown participants"},
    },
)
async def import_meeting(request: Request, ctx: CurrentUser) -> JSONResponse:
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit() or int(declared) > MAX_TRANSCRIPT_BYTES + 65536:
        raise HTTPException(status_code=413, detail="Transcripts are limited to 5 MB.")
    async with request.form(max_files=1, max_fields=1, max_part_size=MAX_METADATA_BYTES) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="A 'file' part is required.")
        metadata = _metadata(form.get("metadata"))
        filename = sanitise_filename(upload.filename or "")
        raw = await upload.read(MAX_TRANSCRIPT_BYTES + 1)
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise HTTPException(status_code=413, detail="Transcripts are limited to 5 MB.")
    try:
        segments = parse_transcript(filename, raw)
    except TranscriptError as exc:
        status = 415 if exc.code == "unsupported_type" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None

    denial = meeting_import_denial(
        ctx,
        business_unit_id=metadata.business_unit_id,
        classification=metadata.classification,
        allow_confidential=request.app.state.settings.allow_confidential_uploads,
    )
    if denial is not None:
        raise HTTPException(status_code=403, detail=_DENIAL_MESSAGE[denial.value])

    participants = sorted({ctx.user_id, *metadata.participant_user_ids})
    source = filename.rsplit(".", 1)[-1].lower()
    data = to_canonical_json(segments)
    validated = ValidatedFile(
        kind=FileKind.TRANSCRIPT,
        media_type=MEDIA_TYPES[FileKind.TRANSCRIPT],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).digest(),
        filename=filename,
    )
    spec = _Spec(metadata, [user_badge(u) for u in participants])
    meeting_id = uuid.uuid4()

    async def resolve_key(s: AsyncSession) -> str:
        nonlocal meeting_id
        known = await existing_user_ids(s, participants)
        if set(participants) - known:
            raise HTTPException(status_code=422, detail="Some participants are not Lekha users.")
        same = await find_same_meeting(
            s,
            source=source,
            external_id=metadata.external_id,
            organiser_user_id=ctx.user_id,
            started_at=metadata.started_at,
        )
        if same is None:
            return f"meeting:{meeting_id}"
        meeting_id = same.meeting_id
        return same.canonical_key

    async def after_write(s: AsyncSession, written: WrittenDocument) -> None:
        if written.is_new:
            await record_meeting(
                s,
                organisation_id=ctx.organisation_id,
                meeting_id=meeting_id,
                document_id=written.document_id,
                started_at=metadata.started_at,
                organiser_user_id=ctx.user_id,
                source=source,
                external_id=metadata.external_id,
            )
        await set_participants(
            s, organisation_id=ctx.organisation_id, meeting_id=meeting_id, user_ids=participants
        )

    result = await create_document(
        request, ctx, spec, validated, data, resolve_key=resolve_key, after_write=after_write
    )
    await enqueue_ingestion(request, ctx.organisation_id, result.ingestion_job_id)
    request.scope.setdefault("state", {})["audit_target"] = ("meeting", str(meeting_id))
    if result.ingestion_job_id is None:  # create_document always creates one
        raise HTTPException(status_code=500, detail="The import could not be queued.")
    body = MeetingImported(
        meeting_id=meeting_id,
        document_id=result.document_id,
        version_number=result.version_number,
        ingestion_job_id=result.ingestion_job_id,
        turns=len(segments),
        speakers=sorted({s.speaker for s in segments if s.speaker}),
    )
    return JSONResponse(status_code=201, content=body.model_dump(mode="json"))


@router.get("/meetings", response_model=list[MeetingOut])
async def list_meetings(request: Request, ctx: CurrentUser) -> list[MeetingOut]:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        meetings = await list_readable_meetings(s, ctx, now=datetime.now(UTC))
    return [
        MeetingOut(
            id=m.meeting_id,
            document_id=m.document_id,
            title=m.title,
            started_at=m.started_at,
            version_number=m.version_number,
            participants=list(m.participants),
        )
        for m in meetings
    ]


@router.get(
    "/meetings/{meeting_id}",
    response_model=MeetingDetailOut,
    responses={404: {"description": "Not found, or you were not a participant"}},
)
async def get_meeting(
    request: Request, ctx: CurrentUser, meeting_id: uuid.UUID
) -> MeetingDetailOut:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        found = await readable_meeting(s, ctx, meeting_id, now=datetime.now(UTC))
    if found is None:
        raise HTTPException(status_code=404, detail="Not found.")
    store: ObjectStore = request.app.state.object_store
    try:
        data, _ = await store.get(found.storage_key)
        segments = from_canonical_json(data)
    except ObjectStoreError:
        logger.exception("meeting_transcript_unavailable")
        raise HTTPException(status_code=503, detail="File storage is unavailable.") from None
    except TranscriptError:
        logger.error("meeting_transcript_unreadable")
        raise HTTPException(status_code=500, detail="The transcript could not be read.") from None
    m = found.summary
    return MeetingDetailOut(
        id=m.meeting_id,
        document_id=m.document_id,
        title=m.title,
        started_at=m.started_at,
        version_number=m.version_number,
        participants=list(m.participants),
        you_organised=found.organiser_user_id == ctx.user_id,
        transcript=[
            TurnOut(
                at=format_time(t.start_ms) if t.start_ms is not None else None,
                start_ms=t.start_ms,
                speaker=t.speaker,
                text=t.text,
            )
            for t in segments
        ],
    )


# --- minutes (plan F-12: drafted by Lekha, approved by a person) ---------------------

MAX_MINUTES_WORDS = 6_000  # what the local model's context comfortably holds


class MinutesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: MinutesTemplate = MinutesTemplate.STANDARD


class DecisionIO(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(default="", max_length=1000)
    at: str | None = None  # when the evidence was said, if the transcript is timed


class ActionIO(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task: str = Field(min_length=1, max_length=500)
    owner: str = Field(default="", max_length=120)
    due: str = Field(default="", max_length=60)
    evidence: str = Field(default="", max_length=1000)
    at: str | None = None
    owner_spoke: bool | None = None  # was the owner one of the speakers?


class MinutesOut(BaseModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    status: MinutesStatus
    template: str
    summary: str
    decisions: list[DecisionIO]
    action_items: list[ActionIO]
    dropped_items: int
    model: str
    edited: bool
    current: bool  # drafted from the meeting's current transcript version?
    created_at: datetime
    decided_at: datetime | None


class MinutesEdit(BaseModel):
    """Optional corrections by the organiser, applied when approving."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(max_length=4000)
    decisions: list[DecisionIO] = Field(max_length=50)
    action_items: list[ActionIO] = Field(max_length=50)


def _minutes_out(m: MeetingMinutes, current_version_id: uuid.UUID) -> MinutesOut:
    return MinutesOut(
        id=m.id,
        meeting_id=m.meeting_id,
        status=m.status,
        template=m.template,
        summary=m.summary,
        decisions=[DecisionIO.model_validate(d) for d in m.decisions],
        action_items=[ActionIO.model_validate(a) for a in m.action_items],
        dropped_items=m.dropped_items,
        model=m.model,
        edited=m.edited,
        current=m.transcript_version_id == current_version_id,
        created_at=m.created_at,
        decided_at=m.decided_at,
    )


async def _meeting_or_404(
    request: Request, ctx: AuthContext, meeting_id: uuid.UUID
) -> MeetingDetail:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        found = await readable_meeting(s, ctx, meeting_id, now=datetime.now(UTC))
    if found is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return found


async def _segments(request: Request, meeting: MeetingDetail) -> list[Segment]:
    store: ObjectStore = request.app.state.object_store
    try:
        data, _ = await store.get(meeting.storage_key)
        return from_canonical_json(data)
    except ObjectStoreError:
        logger.exception("meeting_transcript_unavailable")
        raise HTTPException(status_code=503, detail="File storage is unavailable.") from None
    except TranscriptError:
        logger.error("meeting_transcript_unreadable")
        raise HTTPException(status_code=500, detail="The transcript could not be read.") from None


def _line(segment: Segment) -> str:
    when = f"[{format_time(segment.start_ms)}] " if segment.start_ms is not None else ""
    who = f"{segment.speaker}: " if segment.speaker else ""
    return f"{when}{who}{segment.text}"


def _at(transcript: str, quote: str) -> str | None:
    moment = moment_of_quote(transcript, quote)
    return format_time(moment) if moment is not None else None


@router.post(
    "/meetings/{meeting_id}/minutes",
    status_code=201,
    response_model=MinutesOut,
    responses={
        404: {"description": "Not found, or you were not a participant"},
        422: {"description": "Transcript too long to summarise on this setup"},
        429: {"description": "Too many requests this minute"},
        503: {"description": "No language model configured, or it is unavailable"},
    },
)
async def draft_minutes(
    request: Request, ctx: CurrentUser, meeting_id: uuid.UUID, body: MinutesRequest
) -> MinutesOut:
    """Any participant may ask for a draft; only the organiser can approve it."""
    model: LanguageModel | None = request.app.state.language_model
    if model is None:
        raise HTTPException(status_code=503, detail="No language model is configured.")
    meeting = await _meeting_or_404(request, ctx, meeting_id)
    limiter: RateLimiter = request.app.state.ask_limiter
    try:
        decision = await limiter.hit("minutes", ctx.user_id)
    except RateLimiterUnavailableError:
        raise HTTPException(status_code=503, detail="Please try again shortly.") from None
    if not decision.allowed:
        raise HTTPException(status_code=429, detail="Too many requests; please wait a moment.")

    segments = await _segments(request, meeting)
    lines = [_line(s) for s in segments]
    if sum(len(line.split()) for line in lines) > MAX_MINUTES_WORDS:
        raise HTTPException(
            status_code=422, detail="This transcript is too long to summarise on this setup."
        )
    started = meeting.summary.started_at.strftime("%Y-%m-%d %H:%M UTC")
    settings = request.app.state.settings
    try:
        reply = await model.generate(
            LlmRequest(
                system=MINUTES_SYSTEM_PROMPT,
                user=render_minutes_prompt(meeting.summary.title, started, lines, body.template),
                max_output_tokens=max(settings.llm_max_output_tokens, 1200),
                json_schema=MINUTES_SCHEMA,
            )
        )
    except LanguageModelError:
        logger.warning("minutes_model_unavailable", model=model.model_name)
        raise HTTPException(status_code=503, detail="The language model is unavailable.") from None
    draft = parse_minutes(reply.text)
    if draft is None:
        raise HTTPException(status_code=502, detail="The draft couldn't be read; please try again.")
    transcript = "\n".join(lines)
    verified = verify_minutes(draft, transcript, {s.speaker for s in segments if s.speaker})
    decisions = [
        {"text": d.text, "evidence": d.evidence, "at": _at(transcript, d.evidence)}
        for d in verified.decisions
    ]
    actions = [
        {
            "task": a.task,
            "owner": a.owner,
            "due": a.due,
            "evidence": a.evidence,
            "at": _at(transcript, a.evidence),
            "owner_spoke": a.owner_spoke,
        }
        for a in verified.action_items
    ]

    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        minutes = await record_minutes(
            s,
            organisation_id=ctx.organisation_id,
            meeting_id=meeting_id,
            transcript_version_id=meeting.version_id,
            template=body.template.value,
            summary=verified.summary,
            decisions=decisions,
            action_items=actions,
            dropped_items=verified.dropped,
            model=reply.model,
            drafted_by=ctx.user_id,
        )
        out = _minutes_out(minutes, meeting.version_id)
    request.scope.setdefault("state", {})["audit_target"] = ("meeting_minutes", str(out.id))
    logger.info(
        "minutes_drafted",
        decisions=len(decisions),
        action_items=len(actions),
        dropped=verified.dropped,
    )
    return out


@router.get(
    "/meetings/{meeting_id}/minutes",
    response_model=list[MinutesOut],
    responses={404: {"description": "Not found, or you were not a participant"}},
)
async def list_minutes(
    request: Request, ctx: CurrentUser, meeting_id: uuid.UUID
) -> list[MinutesOut]:
    meeting = await _meeting_or_404(request, ctx, meeting_id)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        return [_minutes_out(m, meeting.version_id) for m in await minutes_of(s, meeting_id)]


async def _decide(
    request: Request,
    ctx: AuthContext,
    minutes_id: uuid.UUID,
    approve: bool,
    edit: MinutesEdit | None,
) -> MinutesOut:
    now = datetime.now(UTC)
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        minutes = await lock_minutes(s, minutes_id)
        meeting = None
        if minutes is not None:
            meeting = await readable_meeting(s, ctx, minutes.meeting_id, now=now)
        if minutes is None or meeting is None:
            raise HTTPException(status_code=404, detail="Not found.")
        if meeting.organiser_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="Only the meeting's organiser can decide.")
        if minutes.status is not MinutesStatus.DRAFT:
            raise HTTPException(status_code=409, detail="These minutes were already decided.")
        if minutes.transcript_version_id != meeting.version_id:
            raise HTTPException(
                status_code=409, detail="A newer transcript was imported; draft the minutes again."
            )
        job_id = None
        if approve:
            edited = edit.model_dump() if edit is not None else None
            await approve_minutes(s, minutes, decided_by=ctx.user_id, now=now, edited=edited)
            job_id = await ingestion_job_of(s, meeting.version_id)
        else:
            await reject_minutes(s, minutes, decided_by=ctx.user_id, now=now)
        out = _minutes_out(minutes, meeting.version_id)
    if job_id is not None:
        # Approved minutes become searchable with the meeting: index the transcript again.
        queue: JobQueue = request.app.state.job_queue
        try:
            await queue.enqueue_reindex(ctx.organisation_id, job_id)
        except (OSError, RedisError):
            logger.exception("minutes_reindex_enqueue_failed")
    request.scope.setdefault("state", {})["audit_target"] = ("meeting_minutes", str(minutes_id))
    return out


@router.post(
    "/minutes/{minutes_id}/approve",
    response_model=MinutesOut,
    responses={
        403: {"description": "Only the organiser may approve"},
        404: {"description": "Not found, or you were not a participant"},
        409: {"description": "Already decided, or the transcript changed since"},
    },
)
async def approve(
    request: Request, ctx: CurrentUser, minutes_id: uuid.UUID, body: MinutesEdit | None = None
) -> MinutesOut:
    return await _decide(request, ctx, minutes_id, True, body)


@router.post(
    "/minutes/{minutes_id}/reject",
    response_model=MinutesOut,
    responses={
        403: {"description": "Only the organiser may reject"},
        404: {"description": "Not found, or you were not a participant"},
        409: {"description": "Already decided, or the transcript changed since"},
    },
)
async def reject(request: Request, ctx: CurrentUser, minutes_id: uuid.UUID) -> MinutesOut:
    return await _decide(request, ctx, minutes_id, False, None)
