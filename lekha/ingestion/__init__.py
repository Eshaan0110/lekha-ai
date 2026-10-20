"""Turning stored documents into searchable, permission-tagged chunks (Phase 2).

Public interface: other modules import from ``lekha.ingestion`` only.
"""

from lekha.ingestion.chunking import Chunk, chunk_sections
from lekha.ingestion.extract import ExtractionError, Section, extract_sections
from lekha.ingestion.language import Language, detect_language

__all__ = [
    "Chunk",
    "ExtractionError",
    "Language",
    "Section",
    "chunk_sections",
    "detect_language",
    "extract_sections",
]
