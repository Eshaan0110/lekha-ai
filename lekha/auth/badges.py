"""Badge (permission-subject) keys.

The single place that formats keys. The database check constraints on
``permission_subject`` enforce exactly these formats, and the same strings are copied into
search-index payloads (``allowed_subjects``) in Phase 2.
"""

from __future__ import annotations

import re
import uuid

_GROUP_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def user_badge(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"


def role_badge(business_unit_id: uuid.UUID, role_id: uuid.UUID) -> str:
    """Role badges always carry their business unit (permissions-v1 §4)."""
    return f"role:{business_unit_id}:{role_id}"


def group_badge(group_name: str) -> str:
    if not _GROUP_NAME.fullmatch(group_name):
        raise ValueError("invalid group name")
    return f"group:{group_name}"
