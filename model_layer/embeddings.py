"""Embedding models behind one interface (CLAUDE.md: interchangeable model providers).

``FakeEmbedder`` is a SIMULATION for development and tests: it hashes words into a fixed
vector so related texts share dimensions, with no download, GPU or network. It is never a
production embedder, and its vectors carry ``model_name="fake-hash-v1"`` so they can't be
mistaken for real ones.

Real models live beside this file: ``bge_m3`` (approved, multilingual) and ``minilm``
(interim, English only). This package must not import anything from ``lekha``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

DIMENSIONS = 1024  # BGE-M3 dense vector size; the fake matches it so indexes are compatible
_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    model_name: str
    dimensions: int
    # Cosine similarity below which a match is too weak to answer from. Each model's scores
    # live on its own scale, so the default is per model (calibrated on the golden set).
    min_relevance: float

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per text, in order."""
        ...


class FakeEmbedder:
    model_name = "fake-hash-v1"
    dimensions = DIMENSIONS
    min_relevance = 0.1

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % DIMENSIONS
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class ModelNotAvailableError(RuntimeError):
    """The real model has not been downloaded or approved."""
