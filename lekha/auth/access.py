"""The access rule: may this caller read this document version?

This is the reference definition (permissions-v1 §3). The database query in
``db/access.py`` implements the same rule in SQL, and an integration test checks that the
two agree on every user/document pair of a test matrix.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from lekha.auth.context import AuthContext
from lekha.auth.model import AccessLevel, Classification


@dataclass(frozen=True, slots=True)
class AclEntry:
    subject_key: str
    access_level: AccessLevel
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DocumentAccessFacts:
    """Everything the rule needs to know about one document version."""

    organisation_id: uuid.UUID
    business_unit_id: uuid.UUID
    classification: Classification
    acl: Iterable[AclEntry]
    document_deleted: bool
    version_deleted: bool
    is_current_version: bool


def can_read(ctx: AuthContext, doc: DocumentAccessFacts, *, now: datetime) -> bool:
    """All five conditions must hold; anything missing or unclear means no."""
    if doc.organisation_id != ctx.organisation_id:  # 1. same organisation
        return False
    clearance = ctx.clearance_by_bu.get(doc.business_unit_id)
    if clearance is None:  # 2. caller holds an active role in this business unit
        return False
    if not doc.classification.within(clearance):  # 3. cleared for its sensitivity
        return False
    if doc.document_deleted or doc.version_deleted or not doc.is_current_version:  # 5. live
        return False
    return any(  # 4. on the guest list with an unexpired read entry
        entry.access_level is AccessLevel.READ
        and entry.subject_key in ctx.badges
        and (entry.expires_at is None or entry.expires_at > now)
        for entry in doc.acl
    )


__all__ = ["AclEntry", "DocumentAccessFacts", "can_read"]
