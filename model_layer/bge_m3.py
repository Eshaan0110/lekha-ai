"""BGE-M3 (BAAI), the approved multilingual embedder, run with ONNX Runtime.

Uses the official ONNX export published in the BAAI/bge-m3 repository (``onnx/``), so no
PyTorch is needed (saves ~2.5 GB). Download (owner OK, 2026-10-20):

    hf download BAAI/bge-m3 --include "onnx/*" --local-dir models/bge-m3

The graph returns ``sentence_embedding``: the [CLS] vector, 1,024 numbers, the dense vector
BGE-M3 is evaluated with. Both model files are checked against the SHA-256 published by the
repository before loading. ``device="cuda"`` uses ONNX Runtime's CUDA provider when the
installed runtime has it, and otherwise falls back to the CPU (logged, never an error).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from model_layer.embeddings import ModelNotAvailableError

logger = logging.getLogger(__name__)

MODEL_NAME = "bge-m3"
DIMENSIONS = 1024
MAX_TOKENS = 8192  # BGE-M3's context; our chunks (≤800 words) stay well inside it
# Published LFS checksums (huggingface.co/BAAI/bge-m3, onnx/), revision 5617a9f6.
_EXPECTED_SHA256 = {
    "model.onnx": "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b",
    "model.onnx_data": "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4",
    "tokenizer.json": "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790",
}
_CHUNK = 8 * 1024 * 1024


class BgeM3Embedder:
    model_name = MODEL_NAME
    dimensions = DIMENSIONS

    def __init__(self, model_dir: Path, device: str = "cpu") -> None:
        onnx_dir = model_dir / "onnx"
        missing = [name for name in _EXPECTED_SHA256 if not (onnx_dir / name).is_file()]
        if missing:
            raise ModelNotAvailableError(
                f"BGE-M3 is not fully downloaded in {onnx_dir} (missing {', '.join(missing)})."
            )
        for name, expected in _EXPECTED_SHA256.items():
            if _sha256(onnx_dir / name) != expected:
                raise ModelNotAvailableError(f"Unexpected {name} in {onnx_dir} (checksum).")
        self._tokenizer = Tokenizer.from_file(str(onnx_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(MAX_TOKENS)
        self._tokenizer.no_padding()
        self.providers = _providers(device)
        self._session = ort.InferenceSession(str(onnx_dir / "model.onnx"), providers=self.providers)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        ids = self._tokenizer.encode(text).ids  # adds <s> … </s>
        input_ids = np.array([ids], dtype=np.int64)
        mask = np.ones_like(input_ids)
        (vector,) = self._session.run(
            ["sentence_embedding"], {"input_ids": input_ids, "attention_mask": mask}
        )
        flat = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(flat)) or 1.0
        return [float(v) for v in flat / norm]


def _providers(device: str) -> list[str]:
    if device == "cuda":
        if "CUDAExecutionProvider" in ort.get_available_providers():
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        logger.warning("bge_m3_cuda_unavailable_using_cpu")
    return ["CPUExecutionProvider"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()
