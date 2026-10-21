"""Reciprocal rank fusion (plan ADR-008, k=60): merge ranked lists without comparing their
raw scores, which live on different scales (cosine similarity vs. text rank)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

RRF_K = 60


def reciprocal_rank_fusion(
    *rankings: Sequence[uuid.UUID], k: int = RRF_K
) -> list[tuple[uuid.UUID, float]]:
    """Each item scores the sum of 1 / (k + rank) over the lists it appears in."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(dict.fromkeys(ranking), start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))
