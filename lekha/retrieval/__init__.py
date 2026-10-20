"""Permission-filtered retrieval over the vector index.

Public interface: other modules import from ``lekha.retrieval`` only.
"""

from lekha.retrieval.filters import (
    MANDATORY_KEYS,
    NoSearchableScopeError,
    build_permission_filter,
    filter_is_permission_scoped,
)
from lekha.retrieval.qdrant_index import (
    Hit,
    IndexedPoint,
    QdrantIndex,
    UnscopedSearchError,
    VectorIndexError,
)

__all__ = [
    "MANDATORY_KEYS",
    "Hit",
    "IndexedPoint",
    "NoSearchableScopeError",
    "QdrantIndex",
    "UnscopedSearchError",
    "VectorIndexError",
    "build_permission_filter",
    "filter_is_permission_scoped",
]
