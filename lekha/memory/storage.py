"""Object storage interface and key layout.

Keys are built only from IDs, never from user input, and are prefixed by organisation and
business unit so storage policies can enforce tenant boundaries (plan: "Object storage
prefixes per tenant and business unit").
"""

from __future__ import annotations

import uuid
from typing import Protocol

from lekha.memory.files import FileKind


class ObjectStoreError(RuntimeError):
    """The object store could not complete the operation."""


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *, media_type: str, sha256: bytes) -> None:
        """Store bytes at ``key``. Raise ObjectStoreError on failure."""
        ...

    async def delete(self, key: str) -> None:
        """Remove ``key`` if present. Raise ObjectStoreError on failure."""
        ...

    async def check(self) -> None:
        """Verify the store is reachable *and* the credentials work. Raise on failure."""
        ...


def object_key(
    *,
    organisation_id: uuid.UUID,
    business_unit_id: uuid.UUID,
    document_id: uuid.UUID,
    version_number: int,
    file_id: uuid.UUID,
    kind: FileKind,
) -> str:
    return (
        f"{organisation_id}/{business_unit_id}/{document_id}/v{version_number}/"
        f"{file_id}.{kind.value}"
    )
