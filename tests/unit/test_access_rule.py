"""The access rule (permissions-v1 §3): each condition has an allow and a deny case."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from lekha.auth import (
    AccessLevel,
    AclEntry,
    AuthContext,
    Classification,
    DocumentAccessFacts,
    RoleGrant,
    build_auth_context,
    can_read,
    group_badge,
    role_badge,
    user_badge,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
ORG, OTHER_ORG = uuid.uuid4(), uuid.uuid4()
ALPHA, BETA = uuid.uuid4(), uuid.uuid4()
VIEWER, LEGAL = uuid.uuid4(), uuid.uuid4()
USER = uuid.uuid4()


def _ctx(*grants: RoleGrant, groups: tuple[str, ...] = ()) -> AuthContext:
    return build_auth_context(
        user_id=USER,
        organisation_id=ORG,
        display_name="Test",
        grants=grants,
        groups=groups,
        now=NOW,
    )


def _grant(
    bu: uuid.UUID,
    role: uuid.UUID,
    clearance: Classification,
    *,
    expires_at: datetime | None = None,
) -> RoleGrant:
    return RoleGrant(
        business_unit_id=bu,
        role_id=role,
        role_name="r",
        max_classification=clearance,
        expires_at=expires_at,
    )


VIEWER_IN_ALPHA = _grant(ALPHA, VIEWER, Classification.INTERNAL)


def _doc(
    *acl: AclEntry,
    organisation_id: uuid.UUID = ORG,
    business_unit_id: uuid.UUID = ALPHA,
    classification: Classification = Classification.INTERNAL,
    document_deleted: bool = False,
    version_deleted: bool = False,
    is_current_version: bool = True,
) -> DocumentAccessFacts:
    """A document readable by an alpha viewer unless an argument says otherwise."""
    return DocumentAccessFacts(
        organisation_id=organisation_id,
        business_unit_id=business_unit_id,
        classification=classification,
        acl=acl or (AclEntry(role_badge(ALPHA, VIEWER), AccessLevel.READ),),
        document_deleted=document_deleted,
        version_deleted=version_deleted,
        is_current_version=is_current_version,
    )


def test_allows_a_reader_who_meets_every_condition() -> None:
    assert can_read(_ctx(VIEWER_IN_ALPHA), _doc(), now=NOW)


@pytest.mark.parametrize(
    ("reason", "doc"),
    [
        ("other organisation", _doc(organisation_id=OTHER_ORG)),
        ("no role in the document's business unit", _doc(business_unit_id=BETA)),
        ("above the reader's clearance", _doc(classification=Classification.CONFIDENTIAL)),
        ("document deleted", _doc(document_deleted=True)),
        ("version deleted", _doc(version_deleted=True)),
        ("not the current version", _doc(is_current_version=False)),
        ("not on the guest list", _doc(AclEntry(role_badge(ALPHA, LEGAL), AccessLevel.READ))),
        (
            "guest-list entry expired",
            _doc(
                AclEntry(
                    role_badge(ALPHA, VIEWER),
                    AccessLevel.READ,
                    expires_at=NOW - timedelta(seconds=1),
                )
            ),
        ),
        (
            "entry grants comment, not read",
            _doc(AclEntry(role_badge(ALPHA, VIEWER), AccessLevel.COMMENT)),
        ),
    ],
)
def test_denies_when_any_condition_fails(reason: str, doc: DocumentAccessFacts) -> None:
    assert not can_read(_ctx(VIEWER_IN_ALPHA), doc, now=NOW), reason


def test_badge_alone_does_not_open_another_business_units_document() -> None:
    # A beta document whose guest list names an alpha badge: an alpha-only reader holds
    # the badge but has no role in beta, so rule 2 still denies (seed case B2).
    beta_doc = _doc(AclEntry(role_badge(ALPHA, VIEWER), AccessLevel.READ), business_unit_id=BETA)
    assert not can_read(_ctx(VIEWER_IN_ALPHA), beta_doc, now=NOW)


def test_cross_unit_invitation_needs_membership_of_the_documents_unit() -> None:
    # The same document is readable once the reader also holds a role in beta: listing a
    # badge is an explicit invitation, and every condition then holds.
    beta_doc = _doc(AclEntry(role_badge(ALPHA, VIEWER), AccessLevel.READ), business_unit_id=BETA)
    reader = _ctx(VIEWER_IN_ALPHA, _grant(BETA, LEGAL, Classification.INTERNAL))
    assert can_read(reader, beta_doc, now=NOW)


def test_user_and_group_badges_grant_access() -> None:
    assert can_read(
        _ctx(VIEWER_IN_ALPHA), _doc(AclEntry(user_badge(USER), AccessLevel.READ)), now=NOW
    )
    assert can_read(
        _ctx(VIEWER_IN_ALPHA, groups=("hr-team",)),
        _doc(AclEntry(group_badge("hr-team"), AccessLevel.READ)),
        now=NOW,
    )


def test_user_without_grants_can_read_nothing() -> None:
    assert not can_read(_ctx(), _doc(AclEntry(user_badge(USER), AccessLevel.READ)), now=NOW)


# --- building the context ----------------------------------------------------------


def test_clearance_is_the_highest_active_grant_per_business_unit() -> None:
    ctx = _ctx(VIEWER_IN_ALPHA, _grant(ALPHA, LEGAL, Classification.CONFIDENTIAL))
    assert ctx.clearance_by_bu == {ALPHA: Classification.CONFIDENTIAL}


def test_expired_grants_contribute_nothing() -> None:
    expired = _grant(ALPHA, VIEWER, Classification.INTERNAL, expires_at=NOW)
    ctx = _ctx(expired)
    assert ctx.clearance_by_bu == {}
    assert ctx.badges == {user_badge(USER)}


def test_badges_cover_user_roles_and_groups() -> None:
    ctx = _ctx(VIEWER_IN_ALPHA, groups=("hr-team",))
    assert ctx.badges == {user_badge(USER), role_badge(ALPHA, VIEWER), group_badge("hr-team")}


@pytest.mark.parametrize("bad", ["", "has space", "x" * 129, "semi;colon"])
def test_invalid_group_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid group name"):
        group_badge(bad)


def test_context_is_immutable() -> None:
    ctx = _ctx(VIEWER_IN_ALPHA)
    with pytest.raises(TypeError):
        ctx.clearance_by_bu[BETA] = Classification.RESTRICTED  # type: ignore[index]
