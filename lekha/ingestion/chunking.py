"""Heading-aware chunking (plan: 400-800 tokens, 50-token overlap, sentence boundaries).

Chunks never cross a section (heading) boundary, so a chunk's context stays intact.
Tokens are approximated by whitespace-separated words; the real tokenizer arrives with the
BGE-M3 embedder. The approximation only affects chunk size, never permissions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from lekha.ingestion.extract import Section
from lekha.ingestion.language import Language, detect_language

TARGET_TOKENS = 500
MAX_TOKENS = 800
OVERLAP_TOKENS = 50
# Sentence ends for English (. ! ?) and Hindi (। danda); Kannada uses ".".
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


@dataclass(frozen=True, slots=True)
class Chunk:
    ordinal: int
    text: str
    heading_path: tuple[str, ...]
    language: Language
    token_count: int
    page: int | None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _split_long(sentence: str) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + MAX_TOKENS]) for i in range(0, len(words), MAX_TOKENS)]


def _windows(sentences: Iterable[str]) -> list[str]:
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(_split_long(sentence) if len(sentence.split()) > MAX_TOKENS else [sentence])
    windows: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and len(" ".join([*current, piece]).split()) > TARGET_TOKENS:
            windows.append(" ".join(current))
            tail = " ".join(current).split()[-OVERLAP_TOKENS:]  # overlap carries context
            current = [" ".join(tail)]
        current.append(piece)
    if current:
        windows.append(" ".join(current))
    return windows


def chunk_sections(sections: Iterable[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        for text in _windows(_sentences(section.text)):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=text,
                    heading_path=section.heading_path,
                    language=detect_language(text),
                    token_count=len(text.split()),
                    page=section.page,
                )
            )
    return chunks
