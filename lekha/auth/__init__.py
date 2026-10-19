"""Identity, permissions, and authorisation context.

Public interface: other modules import from ``lekha.auth`` only, never its submodules.
"""

from lekha.auth.access import AclEntry, DocumentAccessFacts, can_read
from lekha.auth.badges import group_badge, role_badge, user_badge
from lekha.auth.context import AuthContext, RoleGrant, build_auth_context
from lekha.auth.identity import (
    DEV_USER_HEADER,
    DevAuthNotAllowedError,
    DevIdentityProvider,
    IdentityProvider,
    VerifiedIdentity,
)
from lekha.auth.model import AccessLevel, AclSource, Classification, SubjectKind
from lekha.auth.upload import CURATOR_ROLE, UploadDenial, upload_denial

__all__ = [
    "CURATOR_ROLE",
    "DEV_USER_HEADER",
    "AccessLevel",
    "AclEntry",
    "AclSource",
    "AuthContext",
    "Classification",
    "DevAuthNotAllowedError",
    "DevIdentityProvider",
    "DocumentAccessFacts",
    "IdentityProvider",
    "RoleGrant",
    "SubjectKind",
    "UploadDenial",
    "VerifiedIdentity",
    "build_auth_context",
    "can_read",
    "group_badge",
    "role_badge",
    "upload_denial",
    "user_badge",
]
