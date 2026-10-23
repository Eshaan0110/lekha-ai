"""Glossary-aware correction of names that speech recognition got slightly wrong (Phase 5
plan: "glossary-aware correction of names"). Pure: no I/O.

Speech services hear "M P L" for "MPL" or "Manipal Farma Labs" for "Manipal Pharma Labs".
Conservative on purpose: a wrong "correction" changes the question.
- Names of up to 4 characters are fixed only when they match exactly once spaces and dots
  are ignored ("M P L", "m.p.l." → "MPL").
- Longer names may differ slightly (similarity ≥ 0.88, about one wrong letter in eight).
- Only whole runs of 1 to 4 words are replaced, and a run that already is a known name is left
  alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

SHORT_NAME = 4
MIN_SIMILARITY = 0.88
MAX_WINDOW = 4
_WORD = re.compile(r"[^\s]+")
_EDGE_PUNCTUATION = ".,!?;:\"'()।"


@dataclass(frozen=True, slots=True)
class Correction:
    heard: str
    name: str


def _compact(text: str) -> str:
    return re.sub(r"[\s.\-]", "", text).casefold()


def _score(heard: str, name: str) -> float:
    a, b = _compact(heard), _compact(name)
    if not a or not b:
        return 0.0
    if len(b) <= SHORT_NAME:
        return 1.0 if a == b else 0.0
    if not 0.8 <= len(a) / len(b) <= 1.25:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def correct_names(text: str, names: Iterable[str]) -> tuple[str, list[Correction]]:
    """``text`` with near-misses of glossary ``names`` replaced, and what was changed."""
    known = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)
    if not known:
        return text, []
    words = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    out: list[str] = []
    corrections: list[Correction] = []
    cursor = 0  # position in ``text`` copied so far
    i = 0
    while i < len(words):
        best: tuple[float, int, str] | None = None  # (score, words used, name)
        for size in range(min(MAX_WINDOW, len(words) - i), 0, -1):
            start, end = words[i][0], words[i + size - 1][1]
            heard = text[start:end].strip(_EDGE_PUNCTUATION)
            if not heard:
                continue
            if heard in known:  # already a known name: keep it whole, unchanged
                best = (1.0, size, heard)
                break
            for name in known:
                score = _score(heard, name)
                if score >= MIN_SIMILARITY and (best is None or score > best[0]):
                    best = (score, size, name)
        if best is None:
            i += 1
            continue
        _, size, name = best
        start, end = words[i][0], words[i + size - 1][1]
        span = text[start:end]
        heard = span.strip(_EDGE_PUNCTUATION)
        lead = span[: span.index(heard)] if heard else ""
        trail = span[len(lead) + len(heard) :]
        if "." in heard and trail.startswith("."):
            trail = trail[1:]  # "m.p.l." -> "MPL": the last dot belonged to the abbreviation
        if heard != name:
            out.append(text[cursor:start] + lead + name + trail)
            corrections.append(Correction(heard, name))
        else:
            out.append(text[cursor:end])
        cursor = end
        i += size
    out.append(text[cursor:])
    return "".join(out), corrections
