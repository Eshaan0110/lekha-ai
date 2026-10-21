"""all-MiniLM-L6-v2: a small, real, English-only embedding model run on the CPU with numpy.

This is the interim real embedder (owner, 2026-10-20) while the BGE-M3 download waits. It
understands meaning in English only; Kannada and Hindi text get poor vectors, so multilingual
search still needs BGE-M3. Weights are Apache-2.0 (sentence-transformers).

The model is a 6-layer BERT encoder (384 dimensions). Rather than pull in PyTorch (~2.5 GB),
the forward pass is written here in numpy: the maths is fixed by the published architecture,
and the unit tests check the output against reference vectors from sentence-transformers.
Texts longer than the model's 256-token window are split into windows whose vectors are
averaged, so a whole ~500-word chunk counts, not just its first paragraph.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from tokenizers import Tokenizer

from model_layer.embeddings import ModelNotAvailableError

MODEL_NAME = "all-minilm-l6-v2"
DIMENSIONS = 384
# SHA-256 of the exact weights file this code was verified against. A different file is
# refused rather than silently producing different vectors.
WEIGHTS_SHA256 = "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db"
_WINDOW = 256  # the model's max_seq_length (sentence_bert_config.json), including [CLS]/[SEP]
_LAYERS, _HEADS, _EPS = 6, 12, 1e-12

Array = NDArray[np.float32]


class MiniLmEmbedder:
    model_name = MODEL_NAME
    dimensions = DIMENSIONS
    min_relevance = 0.3

    def __init__(self, model_dir: Path) -> None:
        weights_path, tokenizer_path = model_dir / "model.safetensors", model_dir / "tokenizer.json"
        if not weights_path.is_file() or not tokenizer_path.is_file():
            raise ModelNotAvailableError(f"all-MiniLM-L6-v2 is not in {model_dir}.")
        raw = weights_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != WEIGHTS_SHA256:
            raise ModelNotAvailableError(f"Unexpected weights file in {model_dir} (checksum).")
        self._w = _load_safetensors(raw)
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        cls, sep = self._tokenizer.token_to_id("[CLS]"), self._tokenizer.token_to_id("[SEP]")
        if cls is None or sep is None:
            raise ModelNotAvailableError(f"Unexpected tokenizer in {model_dir}.")
        self._cls, self._sep = cls, sep

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        ids = self._tokenizer.encode(text, add_special_tokens=False).ids
        body = _WINDOW - 2
        windows = [ids[i : i + body] for i in range(0, len(ids), body)] or [[]]
        total = np.zeros(DIMENSIONS, dtype=np.float32)
        for window in windows:
            # Weight each window by its length, so the average matches one long mean-pool.
            total += self._encode([self._cls, *window, self._sep]) * (len(window) + 2)
        norm = float(np.linalg.norm(total)) or 1.0
        return [float(v) for v in total / norm]

    def _encode(self, ids: list[int]) -> Array:
        """BERT forward pass for one window; returns the mean of its token vectors."""
        w = self._w
        n = len(ids)
        x = (
            w["embeddings.word_embeddings.weight"][ids]
            + w["embeddings.position_embeddings.weight"][:n]
            + w["embeddings.token_type_embeddings.weight"][0]
        )
        x = _layer_norm(x, w, "embeddings.LayerNorm")
        head = DIMENSIONS // _HEADS
        for i in range(_LAYERS):
            p = f"encoder.layer.{i}."

            def split(t: Array) -> Array:
                return t.reshape(n, _HEADS, head).transpose(1, 0, 2)

            q = split(_linear(x, w, p + "attention.self.query"))
            k = split(_linear(x, w, p + "attention.self.key"))
            v = split(_linear(x, w, p + "attention.self.value"))
            scores = (q @ k.transpose(0, 2, 1) / math.sqrt(head)).astype(np.float32)
            scores = np.exp(scores - scores.max(axis=-1, keepdims=True))
            scores /= scores.sum(axis=-1, keepdims=True)
            context = (scores @ v).transpose(1, 0, 2).reshape(n, DIMENSIONS)
            attention = _linear(context, w, p + "attention.output.dense")
            x = _layer_norm(attention + x, w, p + "attention.output.LayerNorm")
            hidden = _gelu(_linear(x, w, p + "intermediate.dense"))
            x = _layer_norm(_linear(hidden, w, p + "output.dense") + x, w, p + "output.LayerNorm")
        result: Array = x.mean(axis=0)
        return result


def _linear(x: Array, w: dict[str, Array], name: str) -> Array:
    out: Array = x @ w[name + ".weight"].T + w[name + ".bias"]
    return out


def _layer_norm(x: Array, w: dict[str, Array], name: str) -> Array:
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    out = (x - mean) / np.sqrt(var + _EPS) * w[name + ".weight"] + w[name + ".bias"]
    return out.astype(np.float32)


def _gelu(x: Array) -> Array:
    # BERT's "gelu" is the exact erf form. numpy has no erf, so use the Abramowitz-Stegun
    # 7.1.26 approximation (max error 1.5e-7, far below float32 noise in the result).
    z = x / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * np.abs(z))
    poly = t * (
        0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    erf = np.sign(z) * (1.0 - poly * np.exp(-z * z))
    out: Array = (0.5 * x * (1.0 + erf)).astype(np.float32)
    return out


def _load_safetensors(raw: bytes) -> dict[str, Array]:
    """Read the float32 tensors from a .safetensors file (8-byte length, JSON header, data)."""
    (header_len,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8 : 8 + header_len])
    data = memoryview(raw)[8 + header_len :]
    tensors: dict[str, Array] = {}
    for name, info in header.items():
        if name == "__metadata__" or info["dtype"] != "F32":
            continue
        start, end = info["data_offsets"]
        tensors[name] = np.frombuffer(data[start:end], dtype="<f4").reshape(info["shape"])
    return tensors
