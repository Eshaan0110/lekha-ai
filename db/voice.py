"""Persistence for the phone channel. Callers run these inside ``tenant_transaction``."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import (
    ActionExecution,
    ActionProposal,
    AppUser,
    EmployeeContact,
    Escalation,
    OutboxMessage,
    VerificationCode,
    VoiceCall,
    VoiceTurn,
)
from lekha.actions import ProposalStatus
from lekha.speech import CODE_DIGITS, CODE_LIFETIME, CallState, EscalationReason


async def create_call(
    session: AsyncSession, organisation_id: uuid.UUID, caller_number: str, now: datetime
) -> VoiceCall:
    call = VoiceCall(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        caller_number=caller_number,
        state=CallState.AWAITING_ID,
        last_activity_at=now,
    )
    session.add(call)
    await session.flush()
    return call


async def lock_call(session: AsyncSession, call_id: uuid.UUID) -> VoiceCall | None:
    found: VoiceCall | None = await session.scalar(
        select(VoiceCall).where(VoiceCall.id == call_id).with_for_update()
    )
    return found


async def add_turns(
    session: AsyncSession, call: VoiceCall, turns: Sequence[tuple[str, str]]
) -> None:
    """Append (speaker, text) turns to the written transcript. The call must be locked."""
    last = await session.scalar(
        select(func.coalesce(func.max(VoiceTurn.seq), 0)).where(VoiceTurn.call_id == call.id)
    )
    for offset, (speaker, text) in enumerate(turns, start=1):
        session.add(
            VoiceTurn(
                organisation_id=call.organisation_id,
                call_id=call.id,
                seq=(last or 0) + offset,
                speaker=speaker,
                text=text[:4000],
            )
        )
    await session.flush()


def end_call(call: VoiceCall, state: CallState, reason: str, now: datetime) -> None:
    call.state = state
    call.ended_at = now
    call.end_reason = reason


async def escalate(
    session: AsyncSession, call: VoiceCall, reason: EscalationReason, now: datetime
) -> None:
    """Hand the call to the (simulated) human queue; the automated part ends here."""
    session.add(
        Escalation(organisation_id=call.organisation_id, call_id=call.id, reason=reason.value)
    )
    end_call(call, CallState.ESCALATED, reason.value, now)
    await session.flush()


@dataclass(frozen=True, slots=True)
class Contact:
    user_id: uuid.UUID
    phone: str


async def active_contact_by_hr_number(session: AsyncSession, hr_number: str) -> Contact | None:
    row = (
        await session.execute(
            select(EmployeeContact.user_id, EmployeeContact.phone)
            .join(AppUser, AppUser.id == EmployeeContact.user_id)
            .where(EmployeeContact.hr_number == hr_number, AppUser.is_active.is_(True))
        )
    ).first()
    return Contact(row.user_id, row.phone) if row else None


async def contact_of(session: AsyncSession, user_id: uuid.UUID) -> Contact | None:
    phone = await session.scalar(
        select(EmployeeContact.phone).where(EmployeeContact.user_id == user_id)
    )
    return Contact(user_id, phone) if phone else None


async def idp_subject_of(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    subject: str | None = await session.scalar(
        select(AppUser.idp_subject).where(AppUser.id == user_id, AppUser.is_active.is_(True))
    )
    return subject


async def codes_sent_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(VerificationCode)
        .where(VerificationCode.user_id == user_id, VerificationCode.created_at >= since)
    )
    return count or 0


def _hash(salt: bytes, code: str) -> bytes:
    return hashlib.sha256(salt + code.encode()).digest()


async def issue_code(
    session: AsyncSession, call: VoiceCall, user_id: uuid.UUID, now: datetime
) -> str:
    """A fresh random code for this call; only its salted hash is stored."""
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    salt = secrets.token_bytes(16)
    session.add(
        VerificationCode(
            organisation_id=call.organisation_id,
            call_id=call.id,
            user_id=user_id,
            salt=salt,
            code_hash=_hash(salt, code),
            expires_at=now + CODE_LIFETIME,
            created_at=now,
        )
    )
    await session.flush()
    return code


async def redeem_code(
    session: AsyncSession, call: VoiceCall, user_id: uuid.UUID, code: str, now: datetime
) -> bool:
    """True, and the code is used up, if ``code`` matches this call's live code."""
    issued = await session.scalar(
        select(VerificationCode)
        .where(
            VerificationCode.call_id == call.id,
            VerificationCode.user_id == user_id,
            VerificationCode.used_at.is_(None),
            VerificationCode.expires_at > now,
        )
        .order_by(VerificationCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if issued is None or not hmac.compare_digest(issued.code_hash, _hash(issued.salt, code)):
        return False
    issued.used_at = now
    await session.flush()
    return True


async def add_outbox(
    session: AsyncSession, organisation_id: uuid.UUID, contact: Contact, kind: str, body: str
) -> None:
    session.add(
        OutboxMessage(
            organisation_id=organisation_id,
            user_id=contact.user_id,
            phone=contact.phone,
            kind=kind,
            body=body[:4000],
        )
    )
    await session.flush()


@dataclass(frozen=True, slots=True)
class TicketSummary:
    title: str
    status: ProposalStatus
    reference: str | None


async def recent_tickets_of(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 3
) -> list[TicketSummary]:
    rows = await session.execute(
        select(ActionProposal.inputs, ActionProposal.status, ActionExecution.external_id)
        .outerjoin(ActionExecution, ActionExecution.proposal_id == ActionProposal.id)
        .where(ActionProposal.proposed_by == user_id, ActionProposal.tool == "create_ticket")
        .order_by(ActionProposal.created_at.desc())
        .limit(limit)
    )
    return [TicketSummary(str(inputs["title"]), status, ref) for inputs, status, ref in rows]


# --- simulator views (dev only) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnView:
    speaker: str
    text: str
    at: datetime


async def transcript(session: AsyncSession, call_id: uuid.UUID) -> list[TurnView]:
    rows = await session.execute(
        select(VoiceTurn.speaker, VoiceTurn.text, VoiceTurn.created_at)
        .where(VoiceTurn.call_id == call_id)
        .order_by(VoiceTurn.seq)
    )
    return [TurnView(speaker, text, at) for speaker, text, at in rows]


@dataclass(frozen=True, slots=True)
class OutboxView:
    phone: str
    kind: str
    body: str
    at: datetime


async def outbox(session: AsyncSession, phone: str | None, limit: int = 50) -> list[OutboxView]:
    query = select(OutboxMessage).order_by(OutboxMessage.created_at.desc()).limit(limit)
    if phone is not None:
        query = query.where(OutboxMessage.phone == phone)
    return [OutboxView(m.phone, m.kind, m.body, m.created_at) for m in await session.scalars(query)]


@dataclass(frozen=True, slots=True)
class EscalationView:
    call_id: uuid.UUID
    reason: str
    status: str
    at: datetime


async def open_escalations(session: AsyncSession, limit: int = 50) -> list[EscalationView]:
    rows = await session.scalars(
        select(Escalation)
        .where(Escalation.status == "open")
        .order_by(Escalation.created_at.desc())
        .limit(limit)
    )
    return [EscalationView(e.call_id, e.reason, e.status, e.created_at) for e in rows]
