"""Access-control vocabulary shared by the database layer and the permission rules.

Pure Python: no framework, ORM, or network code (CLAUDE.md §4.2).
See docs/design/permissions-v1.md for the model these types describe.
"""

from __future__ import annotations

from enum import StrEnum


class Classification(StrEnum):
    """Document sensitivity, from least to most sensitive.

    Declaration order is significant: it defines the ordering used for clearance checks,
    and the PostgreSQL enum is created in the same order.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return _CLASSIFICATION_ORDER.index(self)

    def within(self, clearance: Classification) -> bool:
        """True if a reader cleared up to ``clearance`` may see this classification."""
        return self.rank <= clearance.rank


_CLASSIFICATION_ORDER: tuple[Classification, ...] = tuple(Classification)


class AccessLevel(StrEnum):
    """What an ACL entry allows. Only ``read`` is enforced in v1."""

    READ = "read"
    COMMENT = "comment"
    CURATE = "curate"


class AclSource(StrEnum):
    """Why an ACL entry exists, for provenance and audit."""

    EXPLICIT = "explicit"
    INHERITED_FROM_SOURCE = "inherited_from_source"
    ROLE_IMPLICIT = "role_implicit"


class SubjectKind(StrEnum):
    """The kinds of badge that can appear on a document's guest list."""

    USER = "user"
    ROLE = "role"  # always scoped to one business unit
    GROUP = "group"  # a group claim from the identity provider
