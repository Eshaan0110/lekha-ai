"""Extraction, language detection, chunking, embeddings and the permission filter."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

import pytest

from lekha.auth import AuthContext, Classification, RoleGrant, build_auth_context
from lekha.ingestion import (
    ExtractionError,
    Language,
    Section,
    chunk_sections,
    detect_language,
    extract_sections,
)
from lekha.ingestion.chunking import MAX_TOKENS, OVERLAP_TOKENS, TARGET_TOKENS
from lekha.memory import FileKind
from lekha.retrieval import (
    MANDATORY_KEYS,
    NoSearchableScopeError,
    build_permission_filter,
    filter_is_permission_scoped,
)
from model_layer import DIMENSIONS, FakeEmbedder
from tests.fixtures.synthetic_docs import make_docx, make_image_only_pdf, make_pdf

# --- language ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Employees get 20 days of annual leave.", Language.ENGLISH),
        ("कर्मचारियों को बीस दिन की छुट्टी मिलती है।", Language.HINDI),
        ("ಉದ್ಯೋಗಿಗಳಿಗೆ ಇಪ್ಪತ್ತು ದಿನಗಳ ರಜೆ ಸಿಗುತ್ತದೆ.", Language.KANNADA),
        ("Leave policy: कर्मचारियों को बीस दिन की छुट्टी मिलती है", Language.HINDI),
        ("12345 !!!", Language.UNKNOWN),
    ],
)
def test_language_is_detected_by_script(text: str, language: Language) -> None:
    assert detect_language(text) is language


# --- extraction ----------------------------------------------------------------------


def test_docx_sections_keep_their_headings() -> None:
    data = make_docx(
        [("Leave", ["Twenty days per year.", "Apply in advance."]), ("Travel", ["Book economy."])]
    )
    sections = extract_sections(FileKind.DOCX, data)
    assert [(s.heading_path, s.text) for s in sections] == [
        (("Leave",), "Twenty days per year.\nApply in advance."),
        (("Travel",), "Book economy."),
    ]


def test_pdf_text_is_extracted_per_page() -> None:
    [section] = extract_sections(FileKind.PDF, make_pdf("Synthetic travel policy text"))
    assert section.page == 1
    assert "Synthetic travel policy text" in section.text


def test_pdf_without_a_text_layer_is_refused() -> None:
    with pytest.raises(ExtractionError) as excinfo:
        extract_sections(FileKind.PDF, make_image_only_pdf())
    assert excinfo.value.code == "no_text_layer"


# --- chunking ------------------------------------------------------------------------


def _words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}." for i in range(n))


def test_chunks_respect_size_overlap_and_sections() -> None:
    sections = [Section(("A",), _words(1200, "a")), Section(("B",), _words(30, "b"))]
    chunks = chunk_sections(sections)
    a_chunks = [c for c in chunks if c.heading_path == ("A",)]
    assert len(a_chunks) >= 2
    assert all(c.token_count <= TARGET_TOKENS + MAX_TOKENS for c in chunks)
    # Consecutive chunks of a section overlap, carrying context forward.
    tail = a_chunks[0].text.split()[-OVERLAP_TOKENS:]
    assert a_chunks[1].text.split()[:OVERLAP_TOKENS] == tail
    # No chunk mixes two sections.
    assert [c.heading_path for c in chunks if "b0." in c.text] == [("B",)]
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_a_single_huge_sentence_is_still_split() -> None:
    chunks = chunk_sections([Section((), " ".join(["x"] * (MAX_TOKENS * 2 + 5)))])
    assert len(chunks) >= 2


# --- embeddings ----------------------------------------------------------------------


def test_fake_embedder_is_deterministic_unit_length_and_labelled() -> None:
    embedder = FakeEmbedder()
    first, again = embedder.embed(["annual leave policy"]), embedder.embed(["annual leave policy"])
    assert first == again
    assert len(first[0]) == DIMENSIONS
    assert math.isclose(sum(v * v for v in first[0]), 1.0, rel_tol=1e-9)
    assert embedder.model_name == "fake-hash-v1"  # never mistaken for the real model


def test_fake_embedder_places_related_texts_closer() -> None:
    leave, leave2, travel = FakeEmbedder().embed(
        ["annual leave days policy", "leave days for annual holidays", "taxi reimbursement rules"]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(leave, leave2) > cosine(leave, travel)


# --- permission filter ---------------------------------------------------------------

NOW = datetime(2026, 10, 20, tzinfo=UTC)
ORG, ALPHA, BETA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _ctx(*grants: RoleGrant) -> AuthContext:
    return build_auth_context(
        user_id=uuid.uuid4(), organisation_id=ORG, display_name="t", grants=grants, now=NOW
    )


def test_filter_requires_a_context() -> None:
    with pytest.raises(PermissionError):
        build_permission_filter(None)


def test_filter_refuses_a_caller_with_no_scope() -> None:
    with pytest.raises(NoSearchableScopeError):
        build_permission_filter(_ctx())


def test_filter_scopes_tenant_badges_units_and_clearance() -> None:
    ctx = _ctx(
        RoleGrant(ALPHA, uuid.uuid4(), "viewer", Classification.INTERNAL),
        RoleGrant(BETA, uuid.uuid4(), "legal", Classification.CONFIDENTIAL),
    )
    query_filter = build_permission_filter(ctx)
    must = {c["key"]: c["match"] for c in query_filter["must"]}
    assert must["tenant_id"] == {"value": str(ORG)}
    assert must["is_current_version"] == {"value": True}
    assert set(must["allowed_subjects"]["any"]) == set(ctx.badges)
    units = {
        clause["must"][0]["match"]["value"]: clause["must"][1]["range"]["lte"]
        for clause in query_filter["should"]
    }
    assert units == {
        str(ALPHA): Classification.INTERNAL.rank,
        str(BETA): Classification.CONFIDENTIAL.rank,
    }
    assert filter_is_permission_scoped(query_filter)


@pytest.mark.parametrize(
    "query_filter",
    [None, {}, {"must": [{"key": "tenant_id", "match": {"value": "x"}}]}, {"should": []}],
)
def test_guard_rejects_filters_missing_mandatory_keys(query_filter: dict[str, object]) -> None:
    assert not filter_is_permission_scoped(query_filter)
    assert {"tenant_id", "allowed_subjects"} == MANDATORY_KEYS
