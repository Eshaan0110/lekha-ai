"""The real embedders: all-MiniLM-L6-v2 (numpy) and BGE-M3 (ONNX Runtime).

Tests that need the weights skip when ``models/all-MiniLM-L6-v2`` is absent (it is
git-ignored); the maths helpers and the safety checks are always tested.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest

from model_layer import BgeM3Embedder, MiniLmEmbedder, ModelNotAvailableError
from model_layer.minilm import _gelu, _load_safetensors

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "all-MiniLM-L6-v2"
needs_model = pytest.mark.skipif(
    not (MODEL_DIR / "model.safetensors").is_file(), reason="all-MiniLM-L6-v2 not present"
)


def test_gelu_matches_the_exact_erf_form() -> None:
    xs = np.linspace(-6, 6, 241, dtype=np.float32)
    exact = [0.5 * x * (1 + math.erf(x / math.sqrt(2))) for x in xs.tolist()]
    assert np.allclose(_gelu(xs), exact, atol=1e-6)


def test_safetensors_reader_returns_named_float_tensors() -> None:
    data = np.arange(6, dtype="<f4").tobytes()
    header = json.dumps(
        {
            "__metadata__": {"format": "pt"},
            "w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]},
        }
    ).encode()
    tensors = _load_safetensors(struct.pack("<Q", len(header)) + header + data)
    assert tensors["w"].tolist() == [[0, 1, 2], [3, 4, 5]]


def test_missing_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelNotAvailableError):
        MiniLmEmbedder(tmp_path)


def test_unexpected_weights_are_refused(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"not the verified weights")
    (tmp_path / "tokenizer.json").write_text("{}")
    with pytest.raises(ModelNotAvailableError, match="checksum"):
        MiniLmEmbedder(tmp_path)


@pytest.fixture(scope="module")
def embedder() -> MiniLmEmbedder:
    return MiniLmEmbedder(MODEL_DIR)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@needs_model
def test_vectors_are_unit_length_labelled_and_deterministic(embedder: MiniLmEmbedder) -> None:
    first, again = embedder.embed(["annual leave"]), embedder.embed(["annual leave"])
    assert first == again
    assert len(first[0]) == 384
    assert math.isclose(_cos(first[0], first[0]), 1.0, rel_tol=1e-5)
    assert embedder.model_name == "all-minilm-l6-v2"


@needs_model
def test_meaning_matches_without_shared_words(embedder: MiniLmEmbedder) -> None:
    question, holiday, taxi = embedder.embed(
        [
            "How many days of annual leave do employees get?",
            "Staff are entitled to 24 days of paid holiday each year.",
            "Taxi fares are reimbursed for business travel.",
        ]
    )
    assert _cos(question, holiday) > 0.5
    assert _cos(question, holiday) > _cos(question, taxi) + 0.3


@needs_model
def test_long_text_uses_every_window(embedder: MiniLmEmbedder) -> None:
    # The topic that only appears after the first 256 tokens must still count.
    filler = "The canteen menu changes weekly. " * 60
    [long_text] = embedder.embed([filler + "Laptops must be encrypted and locked."])
    [canteen_only] = embedder.embed([filler])
    [security] = embedder.embed(["laptop encryption rules"])
    assert _cos(long_text, security) > _cos(canteen_only, security)


# First 8 values for this sentence from the official sentence-transformers library
# (6.1.0, torch CPU), recorded 2026-10-20; the full vectors matched to within 1.2e-7.
REFERENCE_START = [0.063614, 0.02965, 0.024876, 0.061648, 0.02859, 0.039088, -0.022187, -0.076075]


@needs_model
def test_matches_the_official_implementation(embedder: MiniLmEmbedder) -> None:
    [vector] = embedder.embed(["How many days of annual leave do employees get?"])
    assert np.allclose(vector[:8], REFERENCE_START, atol=1e-5)


# --- BGE-M3 (ONNX) ---------------------------------------------------------------------


def test_bge_m3_refuses_an_incomplete_download(tmp_path: Path) -> None:
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx" / "model.onnx").write_bytes(b"graph only")
    with pytest.raises(ModelNotAvailableError, match=r"model.onnx_data"):
        BgeM3Embedder(tmp_path)


def test_bge_m3_refuses_files_that_fail_their_checksum(tmp_path: Path) -> None:
    onnx = tmp_path / "onnx"
    onnx.mkdir()
    for name in ("model.onnx", "model.onnx_data", "tokenizer.json"):
        (onnx / name).write_bytes(b"tampered")
    with pytest.raises(ModelNotAvailableError, match="checksum"):
        BgeM3Embedder(tmp_path)


BGE_DIR = MODEL_DIR.parent / "bge-m3"


@pytest.mark.skipif(
    not (BGE_DIR / "onnx" / "model.onnx_data").is_file(), reason="BGE-M3 not downloaded"
)
def test_bge_m3_matches_meaning_across_english_and_kannada() -> None:
    embedder = BgeM3Embedder(BGE_DIR)
    question_en, answer_kn, unrelated = embedder.embed(
        [
            "How many days of annual leave do employees get?",
            "ನೌಕರರಿಗೆ ಪ್ರತಿ ವರ್ಷ ಇಪ್ಪತ್ತು ದಿನಗಳ ವಾರ್ಷಿಕ ರಜೆ ಸಿಗುತ್ತದೆ.",  # 20 days' annual leave
            "Taxi fares are reimbursed for business travel.",
        ]
    )
    assert len(question_en) == 1024
    assert _cos(question_en, answer_kn) > _cos(question_en, unrelated)
