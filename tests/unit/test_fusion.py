"""Reciprocal rank fusion and keyword-query quoting (pure)."""

from __future__ import annotations

import uuid

from db.search import _or_query
from lekha.retrieval import RRF_K, reciprocal_rank_fusion

A, B, C, D = (uuid.UUID(int=i) for i in range(1, 5))


def test_items_found_by_both_searches_rise_to_the_top() -> None:
    fused = reciprocal_rank_fusion([A, B, C], [C, D])
    # B and D tie (each ranked 2nd once); ties are broken by ID, so results are stable.
    assert [item for item, _ in fused] == [C, A, B, D]
    assert fused[0][1] == 1 / (RRF_K + 3) + 1 / (RRF_K + 1)


def test_a_single_list_keeps_its_order_and_duplicates_count_once() -> None:
    fused = reciprocal_rank_fusion([B, A, B])
    assert [item for item, _ in fused] == [B, A]


def test_empty_inputs_give_nothing() -> None:
    assert reciprocal_rank_fusion([], []) == []


def test_query_words_are_quoted_so_they_are_never_search_syntax() -> None:
    backslash = "\\"
    words = ["leave", "it's", f"a{backslash}b"]
    expected = f"'leave' | 'it''s' | 'a{backslash * 2}b'"
    assert _or_query(words) == expected
