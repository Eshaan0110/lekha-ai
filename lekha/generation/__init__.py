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
from lekha.generation.prompt import (
    ANSWER_SCHEMA,
    SYSTEM_PROMPT,
    SourceBlock,
    neutralise,
    render_user_prompt,
)

__all__ = [
    "ANSWER_SCHEMA",
    "SYSTEM_PROMPT",
    "AbstentionReason",
    "ModelOutput",
    "Outcome",
    "SourceBlock",
    "VerifiedCitation",
    "abstain_message",
    "evidence_is_sufficient",
    "neutralise",
    "parse_model_output",
    "render_user_prompt",
    "span_is_in_source",
    "verify",
]
