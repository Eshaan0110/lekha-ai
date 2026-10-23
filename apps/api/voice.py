"""The phone line, driven by the SIMULATOR (Phase 5 plan D3: no telephony contract yet).

Typed text stands in for speech until step 3. These routes play the part of the telephony
provider's webhook, so they carry no user sign-in: the caller is anonymous until they pass
verification (HR number + one-time code sent to the phone registered with HR; the calling
number is never trusted). They exist only in development sign-in mode (local/test); a real
provider adapter must verify the provider's signed requests before it replaces them.

- POST /api/v1/voice/calls: a call arrives; Lekha plays the consent notice.
- POST /api/v1/voice/calls/{id}/say: one thing the caller says, as text; Lekha's replies.
- POST /api/v1/voice/calls/{id}/speak: the same as audio, through speech-to-text, with the
  replies back through text-to-speech (the labelled fakes until a provider is authorised).
- POST /api/v1/voice/calls/{id}/hangup
- GET  /api/v1/voice/calls/{id}: the written transcript.
- GET  /api/v1/voice/outbox, /api/v1/voice/escalations: the simulated SMS outbox and
  human queue.

Once verified, questions go through exactly the same permission-filtered, grounded /ask
pipeline as the caller's own web sign-in, and tickets through the same proposal and
approval flow. The call row is locked for each step, so steps of one call never interleave
(except while an answer is being written, when the call is free and its state is re-read).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.ask import answer_question
from apps.wiring import Speech
from db.access import load_auth_context
from db.actions import add_proposal
from db.glossary import visible_entries
from db.schema import VoiceCall
from db.tenancy import tenant_transaction
from db.voice import (
    active_contact_by_hr_number,
    add_outbox,
    add_turns,
    codes_sent_since,
    contact_of,
    create_call,
    end_call,
    escalate,
    idp_subject_of,
    issue_code,
    lock_call,
    open_escalations,
    outbox,
    recent_tickets_of,
    redeem_code,
    transcript,
)
from lekha.actions import PROPOSAL_LIFETIME, TicketInputs, can_propose
from lekha.auth import AuthContext, VerifiedIdentity
from lekha.core.config import AuthMode
from lekha.ingestion import Language, detect_language
from lekha.speech import (
    CALL_IDLE_LIMIT,
    CODE_LIFETIME,
    CONSENT_ORDER,
    LANGUAGES,
    MAX_CALL_LENGTH,
    MAX_CODE_ATTEMPTS,
    MAX_CODES_PER_EMPLOYEE_PER_HOUR,
    MAX_ID_ATTEMPTS,
    MAX_UTTERANCE,
    OPEN_STATES,
    CallState,
    EscalationReason,
    Intent,
    Phrase,
    classify,
    correct_names,
    draft_ticket,
    mask_digits,
    parse_code,
    parse_hr_number,
    readback,
    say,
    spoken_answer,
    ticket_status,
)
from model_layer import MAX_AUDIO_BYTES, LanguageModelError, SpeechError, UnsupportedAudioError

logger = structlog.get_logger("lekha.voice")


def phone_gateway(request: Request) -> uuid.UUID:
    """The organisation the simulated phone line serves. Absent (404) unless development
    sign-in is on, which the settings allow only in local and test."""
    settings = request.app.state.settings
    if settings.auth_mode is not AuthMode.DEV or settings.dev_auth_organisation_id is None:
        raise HTTPException(status_code=404, detail="Not found.")
    org: uuid.UUID = settings.dev_auth_organisation_id
    return org


PhoneOrg = Annotated[uuid.UUID, Depends(phone_gateway)]

router = APIRouter(prefix="/api/v1/voice", responses={404: {"description": "Not found"}})


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller_number: str = Field(pattern=r"^\+?[0-9]{3,15}$", description="Untrusted caller ID")


class SayIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MAX_UTTERANCE)


class CallOut(BaseModel):
    id: uuid.UUID
    state: CallState
    language: str | None
    replies: list[str]


class SpeakIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str = Field(max_length=100)
    # Base64 grows data by a third; the decoded size is checked against MAX_AUDIO_BYTES.
    audio_base64: str = Field(min_length=1, max_length=MAX_AUDIO_BYTES * 4 // 3 + 4)


class AudioOut(BaseModel):
    media_type: str
    audio_base64: str


class SpeakOut(CallOut):
    heard: str = Field(description="What speech-to-text heard")
    reply_audio: list[AudioOut] = Field(description="Each reply as speech, in order")


class TurnOut(BaseModel):
    speaker: str
    text: str
    at: datetime


class TranscriptOut(BaseModel):
    id: uuid.UUID
    state: CallState
    turns: list[TurnOut]


class OutboxOut(BaseModel):
    phone: str
    kind: str
    body: str
    at: datetime


class EscalationOut(BaseModel):
    call_id: uuid.UUID
    reason: str
    status: str
    at: datetime


def _target(request: Request, call_id: uuid.UUID) -> None:
    request.scope.setdefault("state", {})["audit_target"] = ("voice_call", str(call_id))


def _out(call: VoiceCall, replies: list[str]) -> CallOut:
    return CallOut(id=call.id, state=call.state, language=call.language, replies=replies)


@router.post("/calls", status_code=201, response_model=CallOut)
async def start_call(request: Request, body: CallIn, org: PhoneOrg) -> JSONResponse:
    now = datetime.now(UTC)
    # Before the caller speaks the language is unknown: the notice plays in all three.
    replies = [f"{say(Phrase.CONSENT, lang)} {say(Phrase.ASK_ID, lang)}" for lang in CONSENT_ORDER]
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await create_call(s, org, body.caller_number, now)
        await add_turns(s, call, [("lekha", r) for r in replies])
        out = _out(call, replies)
    _target(request, call.id)
    logger.info("call_started", call_id=str(call.id))
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@dataclass
class _Step:
    """What one caller utterance led to. ``question`` means: answer it, outside the lock."""

    replies: list[str] = field(default_factory=list)
    question: str | None = None


@router.post(
    "/calls/{call_id}/say",
    response_model=CallOut,
    responses={409: {"description": "The call has ended"}},
)
async def caller_says(request: Request, call_id: uuid.UUID, body: SayIn, org: PhoneOrg) -> CallOut:
    """Typed text, as if already transcribed."""
    _target(request, call_id)
    return await _hear(request, org, call_id, body.text, spoken_language=None)


@router.post(
    "/calls/{call_id}/speak",
    response_model=SpeakOut,
    responses={
        409: {"description": "The call has ended"},
        413: {"description": "Audio too long"},
        415: {"description": "Audio format not accepted by the speech service"},
        503: {"description": "No speech service configured"},
    },
)
async def caller_speaks(
    request: Request, call_id: uuid.UUID, body: SpeakIn, org: PhoneOrg
) -> SpeakOut:
    """Audio through speech-to-text, and Lekha's replies back through text-to-speech."""
    _target(request, call_id)
    speech: Speech | None = request.app.state.speech
    if speech is None:
        raise HTTPException(status_code=503, detail="No speech service is configured.")
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except binascii.Error:
        raise HTTPException(status_code=422, detail="audio_base64 is not valid base64.") from None
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The audio is too long.")
    hint = await _call_language(request, org, call_id)
    try:
        heard = await speech.stt.transcribe(audio, body.media_type, language_hint=hint)
    except UnsupportedAudioError:
        raise HTTPException(status_code=415, detail="Audio format not accepted.") from None
    except SpeechError:
        logger.warning("speech_to_text_failed", model=speech.stt.model_name)
        out = await _fail_call(request, org, call_id)
        return await _with_audio(speech, out, heard="")
    if not heard.text.strip():
        out = await _repeat_request(request, org, call_id)
        return await _with_audio(speech, out, heard="")
    out = await _hear(request, org, call_id, heard.text, spoken_language=heard.language)
    return await _with_audio(speech, out, heard=heard.text)


@router.post(
    "/calls/{call_id}/hangup",
    response_model=CallOut,
    responses={409: {"description": "The call has already ended"}},
)
async def hang_up(request: Request, call_id: uuid.UUID, org: PhoneOrg) -> CallOut:
    _target(request, call_id)
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await _open_call(s, call_id)
        end_call(call, CallState.ENDED, "caller_hung_up", datetime.now(UTC))
        await s.flush()
        return _out(call, [])


@router.get("/calls/{call_id}", response_model=TranscriptOut)
async def get_call(request: Request, call_id: uuid.UUID, org: PhoneOrg) -> TranscriptOut:
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await s.get(VoiceCall, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Not found.")
        turns = await transcript(s, call_id)
        return TranscriptOut(
            id=call.id,
            state=call.state,
            turns=[TurnOut(speaker=t.speaker, text=t.text, at=t.at) for t in turns],
        )


@router.get("/outbox", response_model=list[OutboxOut])
async def read_outbox(request: Request, org: PhoneOrg, phone: str | None = None) -> list[OutboxOut]:
    """SIMULATION: what Lekha would have sent by SMS. Nothing is ever sent."""
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        messages = await outbox(s, phone)
    return [OutboxOut(phone=m.phone, kind=m.kind, body=m.body, at=m.at) for m in messages]


@router.get("/escalations", response_model=list[EscalationOut])
async def read_escalations(request: Request, org: PhoneOrg) -> list[EscalationOut]:
    """SIMULATION: the queue a human agent would pick calls up from."""
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        items = await open_escalations(s)
    return [
        EscalationOut(call_id=e.call_id, reason=e.reason, status=e.status, at=e.at) for e in items
    ]


# --- one step of the conversation ---------------------------------------------------


async def _hear(
    request: Request,
    org: uuid.UUID,
    call_id: uuid.UUID,
    text: str,
    *,
    spoken_language: str | None,
) -> CallOut:
    """One thing the caller said (typed, or transcribed from audio)."""
    now = datetime.now(UTC)
    timed_out = False
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await _open_call(s, call_id)
        if now - call.last_activity_at > CALL_IDLE_LIMIT or now - call.created_at > MAX_CALL_LENGTH:
            end_call(call, CallState.ENDED, "timeout", now)
            timed_out = True
        else:
            if call.language is None:
                call.language = _first_language(text, spoken_language)
            heard = mask_digits(text) if call.state is CallState.AWAITING_CODE else text
            await add_turns(s, call, [("caller", heard)])
            call.last_activity_at = now
            meant = await _correct_names(s, call, text)
            step = await _step(s, call, meant, now)
            await add_turns(s, call, [("lekha", r) for r in step.replies])
            out = _out(call, step.replies)
    if timed_out:
        raise HTTPException(status_code=409, detail="This call has ended.")
    if step.question is None:
        return out
    return await _answer(request, org, call_id, step.question)


def _first_language(text: str, spoken_language: str | None) -> str | None:
    """The call's language, from its first sentence (plan): the speech service's view when
    it gives one we support, else the script the words are written in."""
    if spoken_language in LANGUAGES:
        return spoken_language
    detected = detect_language(text)
    return None if detected is Language.UNKNOWN else detected.value


async def _correct_names(s: AsyncSession, call: VoiceCall, text: str) -> str:
    """Once verified, near-misses of glossary names the caller may see are corrected (the
    transcript keeps what was heard). Before verification nothing is looked up."""
    if call.verified_user_id is None or call.state is CallState.AWAITING_CODE:
        return text
    ctx = await _caller_context(s, call)
    if ctx is None:
        return text
    names = [name for stored in await visible_entries(s, ctx) for name in stored.entry.names]
    meant, corrections = correct_names(text, names)
    if corrections:
        logger.info("names_corrected", call_id=str(call.id), count=len(corrections))
    return meant


async def _call_language(request: Request, org: uuid.UUID, call_id: uuid.UUID) -> str | None:
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await _open_call(s, call_id)
        return call.language


async def _fail_call(request: Request, org: uuid.UUID, call_id: uuid.UUID) -> CallOut:
    """The speech service failed: hand the call to a person."""
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await _open_call(s, call_id)
        step = await _hand_over(s, call, EscalationReason.SYSTEM_FAILURE, datetime.now(UTC))
        await add_turns(s, call, [("lekha", r) for r in step.replies])
        return _out(call, step.replies)


async def _repeat_request(request: Request, org: uuid.UUID, call_id: uuid.UUID) -> CallOut:
    """Nothing was heard: ask again (silence never moves the call on)."""
    async with tenant_transaction(request.app.state.session_factory, org) as s:
        call = await _open_call(s, call_id)
        call.last_activity_at = datetime.now(UTC)
        replies = [say(Phrase.NOT_HEARD, _lang(call))]
        await add_turns(s, call, [("lekha", r) for r in replies])
        return _out(call, replies)


async def _with_audio(speech: Speech, out: CallOut, *, heard: str) -> SpeakOut:
    """Lekha's replies as speech. A text-to-speech failure leaves the audio out (and is
    logged); a real line would then hand the call over, which needs telephony (Phase 0)."""
    audio: list[AudioOut] = []
    try:
        for reply in out.replies:
            spoken = await speech.tts.synthesize(reply, out.language or "en")
            audio.append(
                AudioOut(
                    media_type=spoken.media_type,
                    audio_base64=base64.b64encode(spoken.data).decode("ascii"),
                )
            )
    except SpeechError:
        logger.warning("text_to_speech_failed", model=speech.tts.model_name)
        audio = []
    return SpeakOut(**out.model_dump(), heard=heard, reply_audio=audio)


async def _open_call(s: AsyncSession, call_id: uuid.UUID) -> VoiceCall:
    call = await lock_call(s, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if call.state not in OPEN_STATES:
        raise HTTPException(status_code=409, detail="This call has ended.")
    return call


def _lang(call: VoiceCall) -> str:
    return call.language or "en"


async def _hand_over(
    s: AsyncSession, call: VoiceCall, reason: EscalationReason, now: datetime
) -> _Step:
    await escalate(s, call, reason, now)
    logger.info("call_escalated", call_id=str(call.id), reason=reason.value)
    return _Step([say(Phrase.HANDOVER, _lang(call))])


async def _step(s: AsyncSession, call: VoiceCall, text: str, now: datetime) -> _Step:
    lang = _lang(call)
    intent = classify(text, confirming=call.state is CallState.CONFIRMING_TICKET)
    # A caller can always ask for a person, and distress always reaches one.
    if intent is Intent.HUMAN:
        return await _hand_over(s, call, EscalationReason.CALLER_REQUEST, now)
    if intent is Intent.UPSET:
        return await _hand_over(s, call, EscalationReason.UPSET, now)
    if intent is Intent.GOODBYE and call.state is not CallState.AWAITING_CODE:
        end_call(call, CallState.ENDED, "caller_goodbye", now)
        return _Step([say(Phrase.GOODBYE, lang)])

    if call.state is CallState.AWAITING_ID:
        return await _on_employee_number(s, call, text, now)
    if call.state is CallState.AWAITING_CODE:
        return await _on_code(s, call, text, now)
    if call.state is CallState.DESCRIBING_ISSUE:
        return _on_description(call, text)
    if call.state is CallState.CONFIRMING_TICKET:
        return await _on_confirmation(s, call, intent, now)

    # READY: the caller is verified.
    if call.verified_user_id is None:  # cannot happen in READY; never answer unverified
        return await _hand_over(s, call, EscalationReason.SYSTEM_FAILURE, now)
    if intent is Intent.STATUS:
        tickets = await recent_tickets_of(s, call.verified_user_id)
        if not tickets:
            return _Step([say(Phrase.NO_TICKETS, lang)])
        lines = [ticket_status(t.title, t.status, t.reference, lang) for t in tickets]
        return _Step([" ".join(lines)])
    if intent is Intent.LOG:
        return _on_description(call, text)
    return _Step(question=text)


async def _on_employee_number(s: AsyncSession, call: VoiceCall, text: str, now: datetime) -> _Step:
    lang = _lang(call)
    hr_number = parse_hr_number(text)
    if hr_number is None:
        call.id_attempts += 1
        if call.id_attempts >= MAX_ID_ATTEMPTS:
            return await _hand_over(s, call, EscalationReason.VERIFICATION_FAILED, now)
        return _Step([say(Phrase.ID_NOT_UNDERSTOOD, lang)])
    # The reply is the same whether or not the number exists, is active, or is locked out
    # for sending too many codes, so a caller can't learn which numbers are real.
    contact = await active_contact_by_hr_number(s, hr_number)
    call.claimed_user_id = None
    if contact is not None:
        recent = await codes_sent_since(s, contact.user_id, now - timedelta(hours=1))
        if recent < MAX_CODES_PER_EMPLOYEE_PER_HOUR:
            code = await issue_code(s, call, contact.user_id, now)
            minutes = int(CODE_LIFETIME.total_seconds() // 60)
            await add_outbox(
                s,
                call.organisation_id,
                contact,
                "verification_code",
                f"Lekha code: {code}. It expires in {minutes} minutes. Say it only on a "
                "Lekha call you made yourself.",
            )
            call.claimed_user_id = contact.user_id
        else:
            logger.warning("verification_code_suppressed", call_id=str(call.id))
    call.state = CallState.AWAITING_CODE
    return _Step([say(Phrase.CODE_SENT, lang)])


async def _on_code(s: AsyncSession, call: VoiceCall, text: str, now: datetime) -> _Step:
    lang = _lang(call)
    code = parse_code(text)
    if code is None:
        return _Step([say(Phrase.CODE_NOT_UNDERSTOOD, lang)])
    if call.claimed_user_id is not None and await redeem_code(
        s, call, call.claimed_user_id, code, now
    ):
        call.verified_user_id = call.claimed_user_id
        call.state = CallState.READY
        logger.info("caller_verified", call_id=str(call.id))
        return _Step([say(Phrase.VERIFIED, lang)])
    call.code_attempts += 1
    logger.info("verification_failed", call_id=str(call.id), attempts=call.code_attempts)
    if call.code_attempts >= MAX_CODE_ATTEMPTS:
        return await _hand_over(s, call, EscalationReason.VERIFICATION_FAILED, now)
    return _Step([say(Phrase.WRONG_CODE, lang)])


def _on_description(call: VoiceCall, text: str) -> _Step:
    draft = draft_ticket(text)
    if draft is None:
        call.state = CallState.DESCRIBING_ISSUE
        return _Step([say(Phrase.DESCRIBE_ISSUE, _lang(call))])
    call.pending_ticket = draft.model_dump(mode="json")
    call.state = CallState.CONFIRMING_TICKET
    return _Step([readback(draft, _lang(call))])


async def _on_confirmation(
    s: AsyncSession, call: VoiceCall, intent: Intent, now: datetime
) -> _Step:
    lang = _lang(call)
    if call.pending_ticket is None:  # cannot happen while confirming
        call.state = CallState.READY
        return _Step([say(Phrase.DESCRIBE_ISSUE, lang)])
    ticket = TicketInputs.model_validate(call.pending_ticket)
    if intent is Intent.NO:
        call.pending_ticket = None
        call.state = CallState.READY
        return _Step([say(Phrase.TICKET_CANCELLED, lang)])
    if intent is not Intent.YES:
        return _Step([readback(ticket, lang)])

    call.pending_ticket = None
    call.state = CallState.READY
    ctx = await _caller_context(s, call)
    units = sorted((bu for bu in ctx.roles_by_bu if can_propose(ctx, bu)), key=str) if ctx else []
    if ctx is None or not units:
        return _Step([say(Phrase.CANNOT_LOG, lang)])
    # The caller's confirmation is necessary but not sufficient: this is only a proposal,
    # and a named approver of the unit decides (owner decision D5).
    proposal_id = await add_proposal(
        s,
        organisation_id=call.organisation_id,
        business_unit_id=units[0],
        tool="create_ticket",
        inputs=ticket.model_dump(mode="json"),
        derived_from={"channel": "phone", "call_id": str(call.id)},
        proposed_by=ctx.user_id,
        idempotency_key=uuid.uuid4().hex,
        expires_at=now + PROPOSAL_LIFETIME,
    )
    logger.info("action_proposed", proposal_id=str(proposal_id), channel="phone")
    return _Step([say(Phrase.TICKET_LOGGED, lang)])


async def _caller_context(s: AsyncSession, call: VoiceCall) -> AuthContext | None:
    """The verified caller's permissions, exactly as for their web sign-in."""
    if call.verified_user_id is None:
        return None
    subject = await idp_subject_of(s, call.verified_user_id)
    if subject is None:
        return None  # deactivated since
    return await load_auth_context(
        s, VerifiedIdentity(call.organisation_id, subject), now=datetime.now(UTC)
    )


async def _answer(request: Request, org: uuid.UUID, call_id: uuid.UUID, question: str) -> CallOut:
    """Answer as the verified caller, through the same grounded pipeline as /ask. The call
    lock is not held while the model writes; the call is re-checked afterwards."""
    factory = request.app.state.session_factory
    async with tenant_transaction(factory, org) as s:
        call = await _open_call(s, call_id)
        ctx = await _caller_context(s, call)
    reason: EscalationReason | None = None
    replies: list[str] = []
    sms: str | None = None
    lang = call.language or "en"
    if ctx is None:
        reason = EscalationReason.SYSTEM_FAILURE
    else:
        try:
            result = await answer_question(request, ctx, question)
        except HTTPException as exc:
            if exc.status_code == 429:
                replies = [say(Phrase.TOO_MANY, lang)]
            else:  # no model configured, or the rate limiter unavailable
                reason = EscalationReason.SYSTEM_FAILURE
        except LanguageModelError:
            reason = EscalationReason.SYSTEM_FAILURE
        else:
            if result.abstained:
                # Plan: an out-of-scope question goes to a person.
                replies = [result.answer]
                reason = EscalationReason.OUT_OF_SCOPE
            else:
                replies = [spoken_answer(result.answer), say(Phrase.ANSWER_SENT, lang)]
                sources = "; ".join(f"[{c.number}] {c.title}" for c in result.citations)
                sms = f"Lekha: {result.answer}\nSources: {sources}"
    _target(request, call_id)  # /ask named the query as the target; this request is the call

    async with tenant_transaction(factory, org) as s:
        call = await _open_call(s, call_id)
        now = datetime.now(UTC)
        if reason is not None:
            replies += (await _hand_over(s, call, reason, now)).replies
        if sms is not None and ctx is not None:
            contact = await contact_of(s, ctx.user_id)
            if contact is not None:
                await add_outbox(s, org, contact, "answer", sms)
        call.last_activity_at = now
        await add_turns(s, call, [("lekha", r) for r in replies])
        return _out(call, replies)
