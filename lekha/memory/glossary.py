"""The shared glossary: which names mean the same thing (Phase 4).

"PL", "privilege leave" and "annual leave"; "MPL" and "Manipal Pharma Labs"; "casual leave"
and "ಸಾಂದರ್ಭಿಕ ರಜೆ". When a question uses one name, search also looks for the others, and
the answer model is told they are the same. Pure: no I/O.

Matching is on whole words, ignoring case and extra spaces, so "PL" matches "how many PL do
I get?" but not "PLANT". Word edges are spaces, punctuation or the ends of the text, which
also works for Kannada (whose vowel signs Python doesn't count as letters).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

MAX_TERM_CHARS = 120
MAX_ALIASES = 30
MAX_EXPANSIONS = 5  # entries applied to one question; keeps queries focused
_SPACE = re.compile(r"\s+")


def normalise_term(text: str) -> str:
    """The form names are compared in: NFC, case-folded, single spaces."""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def _is_edge(char: str) -> bool:
    return char.isspace() or unicodedata.category(char)[0] in {"P", "S", "Z"}


def contains_term(text: str, term: str) -> bool:
    """Does ``term`` appear in ``text`` as whole words? Both must already be normalised."""
    if not term:
        return False
    start = text.find(term)
    while start != -1:
        end = start + len(term)
        before_ok = start == 0 or _is_edge(text[start - 1])
        after_ok = end == len(text) or _is_edge(text[end])
        if before_ok and after_ok:
            return True
        start = text.find(term, start + 1)
    return False


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    id: uuid.UUID
    canonical: str
    aliases: tuple[str, ...]
    description: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return (self.canonical, *self.aliases)


@dataclass(frozen=True, slots=True)
class Expansion:
    entry: GlossaryEntry
    matched: str  # the name found in the question


def find_expansions(question: str, entries: Iterable[GlossaryEntry]) -> list[Expansion]:
    """Entries named in the question, longest match first, at most MAX_EXPANSIONS."""
    text = normalise_term(question)
    found: list[Expansion] = []
    for entry in entries:
        matches = [n for n in entry.names if contains_term(text, normalise_term(n))]
        if matches:
            found.append(Expansion(entry, max(matches, key=len)))
    found.sort(key=lambda e: (-len(e.matched), e.entry.canonical))
    return found[:MAX_EXPANSIONS]


def expand_query(question: str, expansions: Iterable[Expansion]) -> str:
    """The question plus the other names of every glossary entry it mentions."""
    text = normalise_term(question)
    extra: list[str] = []
    for expansion in expansions:
        for name in expansion.entry.names:
            if not contains_term(text, normalise_term(name)) and name not in extra:
                extra.append(name)
    return question if not extra else f"{question} ({'; '.join(extra)})"


def glossary_note(expansions: Iterable[Expansion]) -> str:
    """One line per entry for the answer prompt: names that mean the same thing."""
    return "\n".join(
        f"- {' = '.join(e.entry.names)}"
        + (f" ({e.entry.description})" if e.entry.description else "")
        for e in expansions
    )
