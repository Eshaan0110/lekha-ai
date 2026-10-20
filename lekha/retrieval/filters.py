"""The Qdrant permission filter, built only from an AuthContext.

Mirrors ``lekha.auth.can_read``: same tenant, a business unit the caller belongs to, a
classification within their clearance *for that unit*, at least one of their badges on the
guest list, current version only. Permissions are applied inside the search engine, before
any text is returned (CLAUDE.md §6.1). With no context, or a context that can read nothing,
this refuses instead of returning an open filter.
"""

from __future__ import annotations

from typing import Any

from lekha.auth import AuthContext

# Keys every search filter must constrain; the index adapter refuses filters without them.
MANDATORY_KEYS = frozenset({"tenant_id", "allowed_subjects"})


class NoSearchableScopeError(PermissionError):
    """The caller cannot read anything, so there is nothing to search."""


def build_permission_filter(ctx: AuthContext | None) -> dict[str, Any]:  # Any: Qdrant JSON
    if ctx is None:
        raise PermissionError("search requires an authenticated context")
    if not ctx.badges or not ctx.clearance_by_bu:
        raise NoSearchableScopeError("the caller has no readable scope")
    per_unit = [
        {
            "must": [
                {"key": "business_unit_id", "match": {"value": str(bu)}},
                {"key": "classification_rank", "range": {"lte": clearance.rank}},
            ]
        }
        for bu, clearance in sorted(ctx.clearance_by_bu.items())
    ]
    return {
        "must": [
            {"key": "tenant_id", "match": {"value": str(ctx.organisation_id)}},
            {"key": "is_current_version", "match": {"value": True}},
            {"key": "allowed_subjects", "match": {"any": sorted(ctx.badges)}},
        ],
        # Qdrant: when "should" is present, at least one clause must match.
        "should": per_unit,
    }


def filter_is_permission_scoped(query_filter: dict[str, Any] | None) -> bool:
    """True only if the filter's top-level ``must`` constrains every mandatory key."""
    if not query_filter:
        return False
    keys = {
        clause.get("key") for clause in query_filter.get("must", []) if isinstance(clause, dict)
    }
    return keys >= MANDATORY_KEYS
