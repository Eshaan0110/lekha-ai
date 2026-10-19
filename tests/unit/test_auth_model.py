from __future__ import annotations

import pytest

from lekha.auth import Classification


def test_classifications_are_ordered_least_to_most_sensitive() -> None:
    assert [c.value for c in Classification] == [
        "public",
        "internal",
        "confidential",
        "restricted",
    ]


@pytest.mark.parametrize(
    ("document", "clearance", "allowed"),
    [
        (Classification.PUBLIC, Classification.INTERNAL, True),
        (Classification.INTERNAL, Classification.INTERNAL, True),
        (Classification.CONFIDENTIAL, Classification.INTERNAL, False),
        (Classification.RESTRICTED, Classification.CONFIDENTIAL, False),
        (Classification.RESTRICTED, Classification.RESTRICTED, True),
    ],
)
def test_clearance_allows_only_equal_or_lower_classification(
    document: Classification, clearance: Classification, allowed: bool
) -> None:
    assert document.within(clearance) is allowed
