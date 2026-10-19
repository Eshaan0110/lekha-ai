from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.tenancy import tenant_transaction


async def test_tenant_transaction_rejects_anything_but_a_uuid() -> None:
    # Fails closed before any database work, so an unvalidated string can never scope RLS.
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker()
    with pytest.raises(TypeError):
        async with tenant_transaction(factory, "not-a-uuid"):  # type: ignore[arg-type]
            pass
