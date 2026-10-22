"""The phone channel (Phase 5): call rules and what Lekha says. Speech-to-text and
text-to-speech interfaces join in step 3; until then typed text stands in for speech.

Public interface: other modules import from ``lekha.speech`` only.
"""

from lekha.speech.phrases import CONSENT_ORDER, Phrase, readback, say, ticket_status
from lekha.speech.session import (
    CALL_IDLE_LIMIT,
    CODE_DIGITS,
    CODE_LIFETIME,
    MAX_CALL_LENGTH,
    MAX_CODE_ATTEMPTS,
    MAX_CODES_PER_EMPLOYEE_PER_HOUR,
    MAX_ID_ATTEMPTS,
    MAX_UTTERANCE,
    OPEN_STATES,
    CallState,
    EscalationReason,
    Intent,
    classify,
    draft_ticket,
    mask_digits,
    parse_code,
    parse_hr_number,
    spoken_answer,
)

__all__ = [
    "CALL_IDLE_LIMIT",
    "CODE_DIGITS",
    "CODE_LIFETIME",
    "CONSENT_ORDER",
    "MAX_CALL_LENGTH",
    "MAX_CODES_PER_EMPLOYEE_PER_HOUR",
    "MAX_CODE_ATTEMPTS",
    "MAX_ID_ATTEMPTS",
    "MAX_UTTERANCE",
    "OPEN_STATES",
    "CallState",
    "EscalationReason",
    "Intent",
    "Phrase",
    "classify",
    "draft_ticket",
    "mask_digits",
    "parse_code",
    "parse_hr_number",
    "readback",
    "say",
    "spoken_answer",
    "ticket_status",
]
