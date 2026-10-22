"""The phone channel (Phase 5): HR contact details used for verification, calls and their
written transcript, one-time codes, the SIMULATED SMS outbox, and the hand-over queue."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.schema._common import TenantScoped, pg_enum
from lekha.speech import CallState


def _user_fk(column: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", column], ["app_user.organisation_id", "app_user.id"], name=name
    )


def _call_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", "call_id"], ["voice_call.organisation_id", "voice_call.id"], name=name
    )


class EmployeeContact(TenantScoped, Base):
    """HR number and registered phone. SYNTHETIC locally; real values come from HR (blocked
    on Phase 0). Kept apart from ``app_user`` so this personal data can have its own
    retention and access rules."""

    __tablename__ = "employee_contact"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    hr_number: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_employee_contact_user"),
        UniqueConstraint("organisation_id", "hr_number", name="uq_employee_contact_hr_number"),
        _user_fk("user_id", "fk_employee_contact_user"),
        CheckConstraint("hr_number ~ '^MG-[0-9]{5}$'", name="hr_number_format"),
        CheckConstraint("phone ~ '^\\+[0-9]{8,15}$'", name="phone_format"),
    )


class VoiceCall(TenantScoped, Base):
    __tablename__ = "voice_call"

    caller_number: Mapped[str] = mapped_column(Text, nullable=False)  # UNTRUSTED, never used
    state: Mapped[CallState] = mapped_column(pg_enum(CallState, "call_state"), nullable=False)
    language: Mapped[str | None] = mapped_column(Text)  # from the first sentence
    claimed_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # code sent, not verified
    verified_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    id_attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    code_attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    pending_ticket: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # read back, unconfirmed
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("organisation_id", "id", name="uq_voice_call_org_id"),
        _user_fk("claimed_user_id", "fk_voice_call_claimed_user"),
        _user_fk("verified_user_id", "fk_voice_call_verified_user"),
        CheckConstraint("char_length(caller_number) <= 32", name="caller_number_length"),
        CheckConstraint("language IS NULL OR language IN ('en', 'hi', 'kn')", name="language"),
        CheckConstraint(
            "(state IN ('escalated', 'ended')) = (ended_at IS NOT NULL)", name="ended_when_closed"
        ),
        CheckConstraint(
            "end_reason IS NULL OR end_reason ~ '^[a-z_]{1,40}$'", name="end_reason_format"
        ),
    )


class VoiceTurn(TenantScoped, Base):
    """The written transcript of a call (audio is never stored, plan retention default)."""

    __tablename__ = "voice_turn"

    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    seq: Mapped[int] = mapped_column(nullable=False)
    speaker: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("call_id", "seq", name="uq_voice_turn_seq"),
        _call_fk("fk_voice_turn_call"),
        CheckConstraint("speaker IN ('caller', 'lekha')", name="speaker"),
        CheckConstraint("char_length(text) <= 4000", name="text_length"),
    )


class VerificationCode(TenantScoped, Base):
    """A one-time code, stored only as a salted hash."""

    __tablename__ = "verification_code"

    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _call_fk("fk_verification_code_call"),
        _user_fk("user_id", "fk_verification_code_user"),
        Index("ix_verification_code_user_created", "user_id", "created_at"),
    )


class OutboxMessage(TenantScoped, Base):
    """SIMULATION ONLY: messages Lekha would send by SMS. Nothing is ever sent; a dev page
    shows them. Addressed to the employee's registered phone, never the calling number."""

    __tablename__ = "outbox_message"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        _user_fk("user_id", "fk_outbox_message_user"),
        CheckConstraint("kind IN ('verification_code', 'answer', 'ticket')", name="kind_known"),
        CheckConstraint("char_length(body) <= 4000", name="body_length"),
        Index("ix_outbox_message_created", "created_at"),
    )


class Escalation(TenantScoped, Base):
    """A call handed over to a person (the SIMULATED human queue)."""

    __tablename__ = "escalation"

    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")

    __table_args__ = (
        UniqueConstraint("call_id", name="uq_escalation_call"),
        _call_fk("fk_escalation_call"),
        CheckConstraint(
            "reason IN ('caller_request', 'upset', 'out_of_scope', 'system_failure', "
            "'verification_failed')",
            name="reason_known",
        ),
        CheckConstraint("status IN ('open', 'closed')", name="status_known"),
    )
