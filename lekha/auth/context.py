"""AuthContext: who the caller is and what they may see, for one request.

Built fresh on every request from the database (roles can change), then passed explicitly to
every function that touches documents (CLAUDE.md §4.3).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from lekha.auth.badges import group_badge, role_badge, user_badge
from lekha.auth.model import Classification


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """One row of ``user_role_grant`` joined to its role."""

    business_unit_id: uuid.UUID
    role_id: uuid.UUID
    role_name: str
    max_classification: Classification
    expires_at: datetime | None = None

    def active_at(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: uuid.UUID
    organisation_id: uuid.UUID
    display_name: str
    badges: frozenset[str]
    clearance_by_bu: Mapping[uuid.UUID, Classification] = field(
        default_factory=lambda: MappingProxyType({})
    )
    roles_by_bu: Mapping[uuid.UUID, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def business_units(self) -> frozenset[uuid.UUID]:
        return frozenset(self.clearance_by_bu)


def build_auth_context(
    *,
    user_id: uuid.UUID,
    organisation_id: uuid.UUID,
    display_name: str,
    grants: Iterable[RoleGrant],
    groups: Iterable[str] = (),
    now: datetime,
) -> AuthContext:
    """Combine a user's active role grants and IdP groups into one AuthContext.

    Expired grants contribute nothing. A user's clearance in a business unit is the highest
    ``max_classification`` among their active grants there.
    """
    badges = {user_badge(user_id)}
    clearance: dict[uuid.UUID, Classification] = {}
    roles: dict[uuid.UUID, set[str]] = {}
    for grant in grants:
        if not grant.active_at(now):
            continue
        badges.add(role_badge(grant.business_unit_id, grant.role_id))
        roles.setdefault(grant.business_unit_id, set()).add(grant.role_name)
        current = clearance.get(grant.business_unit_id)
        if current is None or grant.max_classification.rank > current.rank:
            clearance[grant.business_unit_id] = grant.max_classification
    badges.update(group_badge(g) for g in groups)
    return AuthContext(
        user_id=user_id,
        organisation_id=organisation_id,
        display_name=display_name,
        badges=frozenset(badges),
        clearance_by_bu=MappingProxyType(clearance),
        roles_by_bu=MappingProxyType({bu: frozenset(r) for bu, r in roles.items()}),
    )
