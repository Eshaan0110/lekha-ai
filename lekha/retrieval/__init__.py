"""Permission-filtered retrieval over the vector index.

Public interface: other modules import from ``lekha.retrieval`` only.
"""

from lekha.retrieval.filters import (
    MANDATORY_KEYS,
    NoSearchableScopeError,
    build_permission_filter,
    filter_is_permission_scoped,
)
from lekha.retrieval.fusion import RRF_K, reciprocal_rank_fusion
from lekha.retrieval.qdrant_index import (
    Hit,
    IndexedPoint,
    QdrantIndex,
    UnscopedSearchError,
    VectorIndexError,
)

__all__ = [
    "MANDATORY_KEYS",
    "RRF_K",
    "Hit",
    "IndexedPoint",
    "NoSearchableScopeError",
    "QdrantIndex",
    "UnscopedSearchError",
    "VectorIndexError",
    "build_permission_filter",
    "filter_is_permission_scoped",
    "reciprocal_rank_fusion",
]
