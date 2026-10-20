"""Pluggable model interfaces (embeddings now; LLM and speech in later phases).

Must not depend on Lekha business modules (CLAUDE.md §4.3).
"""

from model_layer.bge_m3 import BgeM3Embedder
from model_layer.embeddings import (
    DIMENSIONS,
    Embedder,
    FakeEmbedder,
    ModelNotAvailableError,
)
from model_layer.minilm import MiniLmEmbedder

__all__ = [
    "DIMENSIONS",
    "BgeM3Embedder",
    "Embedder",
    "FakeEmbedder",
    "MiniLmEmbedder",
    "ModelNotAvailableError",
]
