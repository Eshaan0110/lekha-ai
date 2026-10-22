"""Phone call rules (Phase 5 plan §4): states, limits and how a caller's words are read.
Pure: no I/O. The orchestration that applies them lives in the API layer.

A call goes: consent notice → employee number → one-time code → ask / log / status →
end, or a hand-over to a person (escalation). Spoken words are untrusted input, like
documents: they choose among fixed paths and never change the rules.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from enum import StrEnum

from lekha.actions import TicketCategory, TicketInputs, TicketPriority

MAX_ID_ATTEMPTS = 3
MAX_CODE_ATTEMPTS = 3
CODE_DIGITS = 6
CODE_LIFETIME = timedelta(minutes=5)
MAX_CODES_PER_EMPLOYEE_PER_HOUR = 3  # stops code guessing across calls, and code flooding
CALL_IDLE_LIMIT = timedelta(minutes=5)
MAX_CALL_LENGTH = timedelta(minutes=20)
SPOKEN_ANSWER_LIMIT = 300  # characters read aloud; the full answer goes by SMS
MAX_UTTERANCE = 1000


class CallState(StrEnum):
    AWAITING_ID = "awaiting_id"
    AWAITING_CODE = "awaiting_code"
    READY = "ready"  # verified: ask, log or status
    DESCRIBING_ISSUE = "describing_issue"
    CONFIRMING_TICKET = "confirming_ticket"
    ESCALATED = "escalated"
    ENDED = "ended"


OPEN_STATES = frozenset(set(CallState) - {CallState.ESCALATED, CallState.ENDED})


class Intent(StrEnum):
    HUMAN = "human"  # asks for a person
    UPSET = "upset"
    GOODBYE = "goodbye"
    LOG = "log"
    STATUS = "status"
    YES = "yes"
    NO = "no"
    QUESTION = "question"


class EscalationReason(StrEnum):
    CALLER_REQUEST = "caller_request"
    UPSET = "upset"
    OUT_OF_SCOPE = "out_of_scope"
    SYSTEM_FAILURE = "system_failure"
    VERIFICATION_FAILED = "verification_failed"


# English words match whole words; Kannada and Hindi words match anywhere (no reliable
# word boundaries for these scripts in Python's regex).
_LEXICON: dict[Intent, tuple[tuple[str, ...], tuple[str, ...]]] = {
    Intent.HUMAN: (
        ("human", "person", "agent", "operator", "someone", "real person", "talk to a person"),
        ("इंसान", "व्यक्ति", "किसी से बात", "ವ್ಯಕ್ತಿ", "ಮನುಷ್ಯ"),
    ),
    Intent.UPSET: (
        ("angry", "useless", "stupid", "rubbish", "nonsense", "worst", "fed up", "pathetic"),
        ("गुस्सा", "बकवास", "बेकार", "ಕೋಪ", "ಅಸಂಬದ್ಧ", "ಪ್ರಯೋಜನವಿಲ್ಲ"),
    ),
    Intent.GOODBYE: (("bye", "goodbye", "that's all", "hang up"), ("अलविदा", "ಬೈ", "ವಿದಾಯ")),
    Intent.LOG: (
        # "report" alone is not enough: "how do I report harassment?" is a question.
        ("complaint", "complain", "ticket"),
        ("शिकायत", "टिकट", "ದೂರು", "ಟಿಕೆಟ್"),
    ),
    # Only about tickets: "what is my leave status?" is a policy question.
    Intent.STATUS: (
        ("ticket status", "complaint status", "status of my ticket", "status of my complaint"),
        ("टिकट की स्थिति", "शिकायत की स्थिति", "ಟಿಕೆಟ್ ಸ್ಥಿತಿ", "ದೂರಿನ ಸ್ಥಿತಿ"),
    ),
    Intent.YES: (("yes", "yeah", "yep", "confirm", "correct", "haan"), ("हाँ", "हां", "ಹೌದು", "ಸರಿ")),
    Intent.NO: (("no", "nope", "cancel", "wrong"), ("नहीं", "ಇಲ್ಲ", "ಬೇಡ")),
}


# "Log a leak", "report that the gate is broken", "I want to raise an issue: ..."
_LOG_PREFIX = re.compile(
    r"^\s*(?:i\s+want\s+to\s+|please\s+)?(?:log|report|raise)\s+(?:a\s+|an\s+)?"
    r"(?:complaint|issue|ticket|problem)?\s*(?:about|that|:|-)?\s*",
    re.IGNORECASE,
)

# "ದೂರು: ...", "शिकायत - ...": the request word at the start, in Kannada or Hindi.
_INDIC_LOG_PREFIX = re.compile(r"^\s*(?:ದೂರು|ಟಿಕೆಟ್|शिकायत|टिकट)\s*[:\-\u2013,।]?\s*")


def _mentions(text: str, intent: Intent) -> bool:
    english, indic = _LEXICON[intent]
    lowered = text.lower()
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in english):
        return True
    return any(word in text for word in indic)


def classify(utterance: str, *, confirming: bool = False) -> Intent:
    """The caller's intent. Requests for a person and distress always win, so a caller
    can leave the automated path at any point."""
    for intent in (Intent.HUMAN, Intent.UPSET):
        if _mentions(utterance, intent):
            return intent
    first: tuple[Intent, ...] = (Intent.YES, Intent.NO) if confirming else ()
    for intent in (*first, Intent.GOODBYE, Intent.STATUS, Intent.LOG):
        if _mentions(utterance, intent):
            return intent
    if _LOG_PREFIX.match(utterance):
        return Intent.LOG
    return Intent.QUESTION


def _digits(utterance: str) -> str:
    """Every digit, in any script (Kannada ೧೨೩ and Devanagari १२३ count too)."""
    return "".join(
        str(unicodedata.digit(char))
        for char in utterance
        if unicodedata.digit(char, None) is not None
    )


def parse_hr_number(utterance: str) -> str | None:
    """Synthetic HR numbers look like MG-10001; callers may say just the digits."""
    digits = _digits(utterance)
    return f"MG-{digits}" if len(digits) == 5 else None


def mask_digits(utterance: str) -> str:
    """What the transcript keeps of an utterance made while a code is expected: never the
    code itself (digits in any script become "#")."""
    return "".join("#" if unicodedata.digit(char, None) is not None else char for char in utterance)


def parse_code(utterance: str) -> str | None:
    digits = _digits(utterance)
    return digits if len(digits) == CODE_DIGITS else None


_CATEGORY_WORDS: tuple[tuple[TicketCategory, tuple[str, ...]], ...] = (
    (TicketCategory.SAFETY, ("fire", "injury", "injured", "smoke", "gas", "spark", "आग", "ಬೆಂಕಿ")),
    (
        TicketCategory.IT,
        ("laptop", "computer", "password", "printer", "wifi", "email", "कंप्यूटर", "ಕಂಪ್ಯೂಟರ್"),
    ),
    (TicketCategory.HR, ("salary", "leave", "payslip", "attendance", "छुट्टी", "ವೇತನ", "ರಜೆ")),
)


def draft_ticket(description: str) -> TicketInputs | None:
    """A ticket draft from what the caller said, read back for confirmation. None if
    nothing usable was said."""
    stripped = _INDIC_LOG_PREFIX.sub("", _LOG_PREFIX.sub("", description, count=1), count=1)
    text = " ".join(stripped.split())
    if len(text) < 3:
        return None
    lowered = text.lower()
    category = next(
        (c for c, words in _CATEGORY_WORDS if any(w in lowered for w in words)),
        TicketCategory.FACILITIES,
    )
    title = text if len(text) <= 80 else text[:77].rsplit(" ", 1)[0] + "..."
    return TicketInputs(
        title=title[0].upper() + title[1:],
        category=category,
        priority=TicketPriority.P2 if category is TicketCategory.SAFETY else TicketPriority.P3,
        description=text[:2000],
    )


_CITATION_MARK = re.compile(r"\s*\[\d+\]")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def spoken_answer(answer: str, limit: int = SPOKEN_ANSWER_LIMIT) -> str:
    """The first whole sentences of a verified answer that fit ``limit``, without citation
    markers (which mean nothing aloud). Never cuts a sentence mid-way except when even the
    first one is too long."""
    text = _CITATION_MARK.sub("", answer).strip()
    spoken = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{spoken} {sentence}".strip()
        if len(candidate) > limit:
            break
        spoken = candidate
    return spoken or text[: limit - 3].rsplit(" ", 1)[0] + "..."
