"""Embedding models behind one interface (CLAUDE.md: interchangeable model providers).

``FakeEmbedder`` is a SIMULATION for development and tests: it hashes words into a fixed
vector so related texts share dimensions, with no download, GPU or network. It is never a
production embedder, and its vectors carry ``model_name="fake-hash-v1"`` so they can't be
mistaken for real ones.

``BgeM3Embedder`` is the approved production model (BAAI/bge-m3, local inference). It loads
only from a local folder the owner has explicitly downloaded (Phase 2 plan, decision D3).
This module must not import anything from ``lekha``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

DIMENSIONS = 1024  # BGE-M3 dense vector size; the fake matches it so indexes are compatible
_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit-length vector per text, in order."""
        ...


class FakeEmbedder:
    model_name = "fake-hash-v1"
    dimensions = DIMENSIONS

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


class BgeM3Embedder:
    """Real BGE-M3. Requires the approved download in ``model_dir`` (not wired up yet)."""

    model_name = "bge-m3"
    dimensions = DIMENSIONS

    def __init__(self, model_dir: Path, device: str = "cpu") -> None:
        if not (model_dir / "config.json").is_file():
            raise ModelNotAvailableError(
                f"BGE-M3 is not in {model_dir}. Downloading it (~2.3 GB) needs owner approval."
            )
        self._model_dir = model_dir
        self._device = device

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ModelNotAvailableError(
            "BGE-M3 inference is enabled after the owner approves the model download."
        )
