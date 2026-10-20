"""Pluggable model interfaces (embeddings now; LLM and speech in later phases).

Must not depend on Lekha business modules (CLAUDE.md §4.3).
"""

from model_layer.embeddings import (
    DIMENSIONS,
    BgeM3Embedder,
    Embedder,
    FakeEmbedder,
    ModelNotAvailableError,
)

__all__ = ["DIMENSIONS", "BgeM3Embedder", "Embedder", "FakeEmbedder", "ModelNotAvailableError"]
