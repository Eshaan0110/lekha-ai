"""Who may upload a document into a business unit (permissions-v1 §5, plan release gate)."""

from __future__ import annotations

import uuid
from enum import StrEnum

from lekha.auth.context import AuthContext
from lekha.auth.model import Classification

CURATOR_ROLE = "curator"
# Above this, data counts as confidential and waits for the Phase 2 release gate.
HIGHEST_UNGATED_CLASSIFICATION = Classification.INTERNAL


class UploadDenial(StrEnum):
    RELEASE_GATE = "release_gate"  # confidential/restricted uploads are switched off
    NOT_CURATOR = "not_curator"  # no active curator role in the target business unit
    ABOVE_CLEARANCE = "above_clearance"  # uploader may not classify above their own clearance


def upload_denial(
    ctx: AuthContext,
    *,
    business_unit_id: uuid.UUID,
    classification: Classification,
    allow_confidential: bool,
) -> UploadDenial | None:
    """None if the upload is allowed; otherwise the first reason it is not."""
    if not allow_confidential and not classification.within(HIGHEST_UNGATED_CLASSIFICATION):
        return UploadDenial.RELEASE_GATE
    if CURATOR_ROLE not in ctx.roles_by_bu.get(business_unit_id, frozenset()):
        return UploadDenial.NOT_CURATOR
    clearance = ctx.clearance_by_bu.get(business_unit_id)
    if clearance is None or not classification.within(clearance):
        return UploadDenial.ABOVE_CLEARANCE
    return None


def can_manage(
    ctx: AuthContext, *, business_unit_id: uuid.UUID, classification: Classification
) -> bool:
    """May this caller change or delete a document? Curators of its business unit, cleared
    for its classification: the same people who may upload it."""
    if CURATOR_ROLE not in ctx.roles_by_bu.get(business_unit_id, frozenset()):
        return False
    clearance = ctx.clearance_by_bu.get(business_unit_id)
    return clearance is not None and classification.within(clearance)


def meeting_import_denial(
    ctx: AuthContext,
    *,
    business_unit_id: uuid.UUID,
    classification: Classification,
    allow_confidential: bool,
) -> UploadDenial | None:
    """None if the caller may import a meeting into this unit. Unlike documents, anyone
    with a role in the unit may import their own meetings (the importer is always one of
    the participants, who are the only readers); the release gate and clearance still apply."""
    if not allow_confidential and not classification.within(HIGHEST_UNGATED_CLASSIFICATION):
        return UploadDenial.RELEASE_GATE
    if not ctx.roles_by_bu.get(business_unit_id):
        return UploadDenial.NOT_CURATOR  # no role at all in this unit
    clearance = ctx.clearance_by_bu.get(business_unit_id)
    if clearance is None or not classification.within(clearance):
        return UploadDenial.ABOVE_CLEARANCE
    return None
