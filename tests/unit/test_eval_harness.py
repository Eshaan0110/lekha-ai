"""The golden-set scoring rules themselves (so a bug can't make a bad run look good)."""

from __future__ import annotations

from tests.eval.golden import QUESTIONS, GoldenQuestion
from tests.eval.harness import QuestionResult, check_safety, score

BY_ID = {q.id: q for q in QUESTIONS}


def _result(question: GoldenQuestion, **fields: object) -> QuestionResult:
    values: dict[str, object] = {
        "abstained": False,
        "reason": None,
        "answer": "",
        "cited": [],
        "spans": [],
        "retrieved": [],
        "latency_s": 1.0,
    }
    values.update(fields)
    return QuestionResult(question=question, **values)  # type: ignore[arg-type]


def test_citing_or_quoting_an_unreadable_document_is_a_violation() -> None:
    probe = _result(BY_ID["X01"], answer="The band is 18 lakh to 26 lakh.", cited=["M01"])
    check_safety(probe)
    assert "cited M01" in probe.leaks
    assert any("18 lakh" in leak for leak in probe.leaks)
    assert set(probe.forbidden_hits) == {"18 lakh", "26 lakh"}
    assert score([probe]).permission_violations == 1


def test_a_reader_quoting_their_own_restricted_document_is_fine() -> None:
    allowed = _result(BY_ID["A53"], answer="18 lakh to 26 lakh [1]", cited=["M01"])
    check_safety(allowed)
    assert allowed.leaks == []


def test_citation_markers_are_not_counted_as_unsupported_numbers() -> None:
    answered = _result(BY_ID["A01"], answer="24 days of paid annual leave [1].", cited=["P01"])
    board = score([answered])
    assert board.faithfulness == 1.0
    assert board.answer_accuracy == 1.0
    assert board.citation_correctness == 1.0


def test_a_number_the_source_never_said_is_unfaithful() -> None:
    invented = _result(BY_ID["A01"], answer="30 days [1].", cited=["P01"])
    board = score([invented])
    assert board.faithfulness == 0.0
    assert board.answer_accuracy == 0.0


def test_abstention_and_recall_are_scored_by_kind() -> None:
    results = [
        _result(BY_ID["U01"], abstained=True, answer="I do not have enough information."),
        _result(BY_ID["U02"], answer="Yes [1].", cited=["P13"]),
        _result(BY_ID["A01"], abstained=True, retrieved=["P01", "P02"]),
    ]
    board = score(results)
    assert board.abstention_correctness == 0.5
    assert board.over_abstention == 1.0
    assert board.recall_at_10 == 1.0
