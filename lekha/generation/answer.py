"""Reading the model's reply, verifying its citations, and deciding to abstain.

Plan: "For each citation, confirm the quoted span appears in the cited source (exact match
or near-exact after whitespace normalisation). If a cited span is not in the source, drop
that citation and (if no citations remain) move to abstention." Markers like [3] for a
dropped citation are removed from the answer text too, so the answer never implies a source
supports something it doesn't.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lekha.generation.prompt import SourceBlock, neutralise

MIN_SPAN_CHARS = 8  # a span must be a real quotation, not "the" or "20"
_MARKER = re.compile(r"\[(\d{1,3})\]")
_SPACE = re.compile(r"\s+")
_QUOTES = "\"'`“”‘’«»"  # noqa: RUF001 - straight, curly and angle quotes


class AbstentionReason(StrEnum):
    NO_SOURCES = "no_sources"  # nothing the asker may read matched
    WEAK_EVIDENCE = "weak_evidence"  # best match below the relevance threshold
    MODEL_ABSTAINED = "model_abstained"
    NO_VERIFIED_CITATIONS = "no_verified_citations"  # every citation failed verification
    INVALID_MODEL_OUTPUT = "invalid_model_output"


ABSTAIN_MESSAGES = {
    "en": "I do not have enough information to answer this.",
    "kn": "ಇದಕ್ಕೆ ಉತ್ತರಿಸಲು ನನ್ನ ಬಳಿ ಸಾಕಷ್ಟು ಮಾಹಿತಿ ಇಲ್ಲ.",
}


def abstain_message(language: str) -> str:
    return ABSTAIN_MESSAGES.get(language, ABSTAIN_MESSAGES["en"])


class _Citation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: int = Field(ge=1, le=100)
    span: str = Field(max_length=2000)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer: str = Field(default="", max_length=8000)
    citations: list[_Citation] = Field(default_factory=list, max_length=30)
    abstained: bool = False
    follow_ups: list[str] = Field(default_factory=list, max_length=5)


def parse_model_output(text: str) -> ModelOutput | None:
    """The JSON object in the reply, or None if there isn't a valid one."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return ModelOutput.model_validate(json.loads(text[start : end + 1]))
    except (ValueError, ValidationError):
        return None


@dataclass(frozen=True, slots=True)
class VerifiedCitation:
    source: SourceBlock
    span: str


@dataclass(frozen=True, slots=True)
class Outcome:
    """Either an answer with at least one verified citation, or an abstention."""

    answer: str | None
    citations: tuple[VerifiedCitation, ...]
    abstention: AbstentionReason | None
    dropped_citations: int = 0


def _normalise(text: str) -> str:
    return _SPACE.sub(" ", text).strip().strip(_QUOTES).rstrip(".…").strip().casefold()


def span_is_in_source(span: str, source: SourceBlock) -> bool:
    wanted = _normalise(span)
    if len(wanted) < MIN_SPAN_CHARS or " " not in wanted:
        return False
    return wanted in _normalise(neutralise(source.text))


def verify(output: ModelOutput, sources: list[SourceBlock]) -> Outcome:
    if output.abstained:
        return Outcome(None, (), AbstentionReason.MODEL_ABSTAINED)
    by_number = {s.number: s for s in sources}
    verified: list[VerifiedCitation] = []
    seen: set[tuple[int, str]] = set()
    for citation in output.citations:
        source = by_number.get(citation.source)
        key = (citation.source, _normalise(citation.span))
        if source is None or key in seen or not span_is_in_source(citation.span, source):
            continue
        seen.add(key)
        verified.append(VerifiedCitation(source, citation.span.strip()))
    dropped = len(output.citations) - len(verified)
    answer = output.answer.strip()
    if not verified or not answer:
        return Outcome(None, (), AbstentionReason.NO_VERIFIED_CITATIONS, dropped)
    supported = {c.source.number for c in verified}
    answer = _MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in supported else "", answer)
    return Outcome(_SPACE.sub(" ", answer).strip(), tuple(verified), None, dropped)


def evidence_is_sufficient(best_score: float | None, min_score: float) -> bool:
    return best_score is not None and best_score >= min_score
