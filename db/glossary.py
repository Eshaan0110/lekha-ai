"""Persistence for the glossary. Callers run these inside ``tenant_transaction``.

Visibility: a shared entry is visible to everyone holding a role anywhere in the
organisation; an unshared one only to people with a role in its business unit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.schema import GlossaryEntry as EntryRow
from db.schema import GlossaryName
from lekha.auth import AuthContext
from lekha.memory import GlossaryEntry, normalise_term


@dataclass(frozen=True, slots=True)
class StoredEntry:
    entry: GlossaryEntry
    business_unit_id: uuid.UUID
    shared: bool


def _visible(ctx: AuthContext) -> ColumnElement[bool] | None:
    units = [bu for bu, roles in ctx.roles_by_bu.items() if roles]
    if not units:
        return None
    return or_(EntryRow.shared.is_(True), EntryRow.business_unit_id.in_(units))


async def visible_entries(session: AsyncSession, ctx: AuthContext) -> list[StoredEntry]:
    condition = _visible(ctx)
    if condition is None:
        return []
    rows = (
        await session.execute(
            select(EntryRow, GlossaryName.name)
            .join(GlossaryName, GlossaryName.entry_id == EntryRow.id)
            .where(condition)
            .order_by(EntryRow.canonical, GlossaryName.name)
        )
    ).all()
    grouped: dict[uuid.UUID, tuple[EntryRow, list[str]]] = {}
    for row, name in rows:
        grouped.setdefault(row.id, (row, []))[1].append(name)
    return [
        StoredEntry(
            GlossaryEntry(
                row.id,
                row.canonical,
                tuple(n for n in names if n != row.canonical),
                row.description,
            ),
            row.business_unit_id,
            row.shared,
        )
        for row, names in grouped.values()
    ]


async def entry_row(session: AsyncSession, entry_id: uuid.UUID) -> EntryRow | None:
    found: EntryRow | None = await session.scalar(
        select(EntryRow).where(EntryRow.id == entry_id).with_for_update()
    )
    return found


async def _set_names(
    session: AsyncSession, organisation_id: uuid.UUID, entry_id: uuid.UUID, names: Sequence[str]
) -> None:
    await session.execute(delete(GlossaryName).where(GlossaryName.entry_id == entry_id))
    await session.flush()
    session.add_all(
        GlossaryName(
            organisation_id=organisation_id,
            entry_id=entry_id,
            name=name,
            name_norm=normalise_term(name),
        )
        for name in names
    )
    await session.flush()  # a name already used elsewhere fails here (IntegrityError)


async def create_entry(
    session: AsyncSession,
    *,
    organisation_id: uuid.UUID,
    business_unit_id: uuid.UUID,
    canonical: str,
    aliases: Sequence[str],
    description: str,
    shared: bool,
    created_by: uuid.UUID,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    session.add(
        EntryRow(
            id=entry_id,
            organisation_id=organisation_id,
            business_unit_id=business_unit_id,
            canonical=canonical,
            description=description,
            shared=shared,
            created_by=created_by,
        )
    )
    await session.flush()
    await _set_names(session, organisation_id, entry_id, [canonical, *aliases])
    return entry_id


async def update_entry(
    session: AsyncSession,
    row: EntryRow,
    *,
    canonical: str,
    aliases: Sequence[str],
    description: str,
    shared: bool,
) -> None:
    row.canonical = canonical
    row.description = description
    row.shared = shared
    await _set_names(session, row.organisation_id, row.id, [canonical, *aliases])


async def delete_entry(session: AsyncSession, row: EntryRow) -> None:
    await session.execute(delete(GlossaryName).where(GlossaryName.entry_id == row.id))
    await session.delete(row)
