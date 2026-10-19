"""Identity providers: turn an incoming request's credentials into a verified identity.

``IdentityProvider`` is the seam where Group OIDC will plug in. Until the Group IdP is
available (Phase 0 dependency), ``DevIdentityProvider`` stands in for local development.
It is a simulation, never a production implementation.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

DEV_USER_HEADER = "x-dev-user"
_DEV_SUBJECT = re.compile(r"^dev\|[a-z0-9][a-z0-9._-]{0,62}$")
DEV_ALLOWED_ENVIRONMENTS = frozenset({"local", "test"})


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Who the identity provider says the caller is. Not yet an authorisation decision."""

    organisation_id: uuid.UUID
    idp_subject: str
    groups: frozenset[str] = frozenset()


class IdentityProvider(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> VerifiedIdentity | None:
        """Return the verified identity, or None if the request carries no valid credentials."""
        ...


class DevAuthNotAllowedError(RuntimeError):
    """Raised when the dev adapter is configured outside a local or test environment."""


class DevIdentityProvider:
    """LOCAL DEVELOPMENT ONLY: trusts an ``X-Dev-User: dev|<name>`` header.

    Anyone who can send that header can act as any seeded user, which is why construction
    fails unless the environment is ``local`` or ``test``. Only subjects starting with
    ``dev|`` are accepted, so real IdP subjects can never be impersonated through it.
    """

    def __init__(self, *, environment: str, organisation_id: uuid.UUID) -> None:
        if environment not in DEV_ALLOWED_ENVIRONMENTS:
            raise DevAuthNotAllowedError(
                f"the dev sign-in adapter is forbidden in the '{environment}' environment"
            )
        self._organisation_id = organisation_id

    def authenticate(self, headers: Mapping[str, str]) -> VerifiedIdentity | None:
        subject = headers.get(DEV_USER_HEADER)
        if subject is None or not _DEV_SUBJECT.fullmatch(subject):
            return None
        return VerifiedIdentity(organisation_id=self._organisation_id, idp_subject=subject)
