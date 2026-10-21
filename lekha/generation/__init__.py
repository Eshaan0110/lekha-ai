"""Answer generation: the fixed prompt, citation verification and abstention.

Pure logic: no network, database or model calls (those live in apps/ and model_layer/).
Public interface: other modules import from ``lekha.generation`` only.
"""

from lekha.generation.answer import (
    AbstentionReason,
    ModelOutput,
    Outcome,
    VerifiedCitation,
    abstain_message,
    evidence_is_sufficient,
    parse_model_output,
    span_is_in_source,
    verify,
)
from lekha.generation.minutes import (
    MINUTES_SCHEMA,
    MINUTES_SYSTEM_PROMPT,
    ActionItem,
    Decision,
    MinutesDraft,
    MinutesTemplate,
    VerifiedMinutes,
    minutes_as_text,
    parse_minutes,
    quote_in_transcript,
    render_minutes_prompt,
    verify_minutes,
)
from lekha.generation.prompt import (
    ANSWER_SCHEMA,
    SYSTEM_PROMPT,
    SourceBlock,
    neutralise,
    render_user_prompt,
)

__all__ = [
    "ANSWER_SCHEMA",
    "MINUTES_SCHEMA",
    "MINUTES_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "AbstentionReason",
    "ActionItem",
    "Decision",
    "MinutesDraft",
    "MinutesTemplate",
    "ModelOutput",
    "Outcome",
    "SourceBlock",
    "VerifiedCitation",
    "VerifiedMinutes",
    "abstain_message",
    "evidence_is_sufficient",
    "minutes_as_text",
    "neutralise",
    "parse_minutes",
    "parse_model_output",
    "quote_in_transcript",
    "render_minutes_prompt",
    "render_user_prompt",
    "span_is_in_source",
    "verify",
    "verify_minutes",
]
