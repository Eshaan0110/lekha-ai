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
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.uploads import WrittenDocument, create_document, enqueue_ingestion
from db.meetings import (
    existing_user_ids,
    find_same_meeting,
    list_readable_meetings,
    readable_meeting,
    record_meeting,
    set_participants,
)
from db.tenancy import tenant_transaction
from lekha.auth import Classification, meeting_import_denial, user_badge
from lekha.ingestion import (
    TranscriptError,
    format_time,
    from_canonical_json,
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
