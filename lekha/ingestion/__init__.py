"""Turning stored documents into searchable, permission-tagged chunks (Phase 2).

Public interface: other modules import from ``lekha.ingestion`` only.
"""

from lekha.ingestion.chunking import Chunk, chunk_sections
from lekha.ingestion.extract import ExtractionError, Section, extract_sections
from lekha.ingestion.language import Language, detect_language
from lekha.ingestion.transcript import (
    Segment,
    TranscriptError,
    chunk_transcript,
    format_time,
    from_canonical_json,
    moment_of_quote,
    parse_transcript,
    to_canonical_json,
)

__all__ = [
    "Chunk",
    "ExtractionError",
    "Language",
    "Section",
    "Segment",
    "TranscriptError",
    "chunk_sections",
    "chunk_transcript",
    "detect_language",
    "extract_sections",
    "format_time",
    "from_canonical_json",
    "moment_of_quote",
    "parse_transcript",
    "to_canonical_json",
]
