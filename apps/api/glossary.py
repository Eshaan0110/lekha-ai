"""The shared glossary: GET/POST /api/v1/glossary, PATCH/DELETE /api/v1/glossary/{id}.

Curators of a business unit manage its entries. Everyone with a role in that unit sees
them, and everyone in the organisation sees entries marked shared. Search and answers use
the entries the asker can see.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from apps.api.auth import CurrentUser, require_auth_context
from db.glossary import (
    StoredEntry,
    create_entry,
    delete_entry,
    entry_row,
    update_entry,
    visible_entries,
)
from db.tenancy import tenant_transaction
from lekha.auth import CURATOR_ROLE, AuthContext
from lekha.memory import MAX_ALIASES, MAX_TERM_CHARS, normalise_term

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)


def _clean(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > MAX_TERM_CHARS:
        raise ValueError(f"names must be 1 to {MAX_TERM_CHARS} characters")
    return cleaned


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical: str
    aliases: list[str] = Field(default_factory=list, max_length=MAX_ALIASES)
    description: str = Field(default="", max_length=500)
    shared: bool = False

    @field_validator("canonical")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return _clean(value)

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, values: list[str]) -> list[str]:
        return [_clean(v) for v in values]


class NewEntry(EntryIn):
    business_unit_id: uuid.UUID


class EntryOut(BaseModel):
    id: uuid.UUID
    canonical: str
    aliases: list[str]
    description: str
    business_unit_id: uuid.UUID
    shared: bool
    can_edit: bool


def _distinct_names(entry: EntryIn) -> list[str]:
    """Aliases without repeats (or repeats of the canonical name), compared normalised."""
    seen = {normalise_term(entry.canonical)}
    aliases: list[str] = []
    for alias in entry.aliases:
        if normalise_term(alias) not in seen:
            seen.add(normalise_term(alias))
            aliases.append(alias)
    return aliases


def _curates(ctx: AuthContext, business_unit_id: uuid.UUID) -> bool:
    return CURATOR_ROLE in ctx.roles_by_bu.get(business_unit_id, frozenset())


def _out(stored: StoredEntry, ctx: AuthContext) -> EntryOut:
    return EntryOut(
        id=stored.entry.id,
        canonical=stored.entry.canonical,
        aliases=list(stored.entry.aliases),
        description=stored.entry.description,
        business_unit_id=stored.business_unit_id,
        shared=stored.shared,
        can_edit=_curates(ctx, stored.business_unit_id),
    )


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="One of these names is already in the glossary.")


@router.get("/glossary", response_model=list[EntryOut])
async def list_glossary(request: Request, ctx: CurrentUser) -> list[EntryOut]:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        return [_out(e, ctx) for e in await visible_entries(s, ctx)]


@router.post(
    "/glossary",
    status_code=201,
    response_model=EntryOut,
    responses={
        403: {"description": "Only curators of the unit manage its glossary"},
        409: {"description": "A name is already used by another entry"},
    },
)
async def add_entry(request: Request, ctx: CurrentUser, body: NewEntry) -> EntryOut:
    if not _curates(ctx, body.business_unit_id):
        raise HTTPException(
            status_code=403, detail="Only curators of this unit manage its glossary."
        )
    aliases = _distinct_names(body)
    try:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            entry_id = await create_entry(
                s,
                organisation_id=ctx.organisation_id,
                business_unit_id=body.business_unit_id,
                canonical=body.canonical,
                aliases=aliases,
                description=body.description,
                shared=body.shared,
                created_by=ctx.user_id,
            )
    except IntegrityError:
        raise _conflict() from None
    request.scope.setdefault("state", {})["audit_target"] = ("glossary_entry", str(entry_id))
    return EntryOut(
        id=entry_id,
        canonical=body.canonical,
        aliases=aliases,
        description=body.description,
        business_unit_id=body.business_unit_id,
        shared=body.shared,
        can_edit=True,
    )


@router.patch(
    "/glossary/{entry_id}",
    response_model=EntryOut,
    responses={
        403: {"description": "Only curators of the entry's unit may change it"},
        404: {"description": "Not found"},
        409: {"description": "A name is already used by another entry"},
    },
)
async def change_entry(
    request: Request, ctx: CurrentUser, entry_id: uuid.UUID, body: EntryIn
) -> EntryOut:
    aliases = _distinct_names(body)
    try:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            row = await entry_row(s, entry_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Not found.")
            if not _curates(ctx, row.business_unit_id):
                raise HTTPException(
                    status_code=403, detail="Only curators of this unit may change it."
                )
            await update_entry(
                s,
                row,
                canonical=body.canonical,
                aliases=aliases,
                description=body.description,
                shared=body.shared,
            )
            unit = row.business_unit_id
    except IntegrityError:
        raise _conflict() from None
    return EntryOut(
        id=entry_id,
        canonical=body.canonical,
        aliases=aliases,
        description=body.description,
        business_unit_id=unit,
        shared=body.shared,
        can_edit=True,
    )


@router.delete(
    "/glossary/{entry_id}",
    status_code=204,
    responses={
        403: {"description": "Only curators of the entry's unit"},
        404: {"description": "Not found"},
    },
)
async def remove_entry(request: Request, ctx: CurrentUser, entry_id: uuid.UUID) -> Response:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        row = await entry_row(s, entry_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found.")
        if not _curates(ctx, row.business_unit_id):
            raise HTTPException(status_code=403, detail="Only curators of this unit may delete it.")
        await delete_entry(s, row)
    return Response(status_code=204)
