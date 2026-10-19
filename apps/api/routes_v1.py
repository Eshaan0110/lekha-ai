"""Versioned API. Every route here requires a signed-in caller (router-level dependency)."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from apps.api.auth import CurrentUser, require_auth_context
from db.access import ReadableDocument, get_readable_document, list_readable_documents
from db.tenancy import tenant_transaction
from lekha.auth import Classification

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)

MAX_PAGE_SIZE = 100


class BusinessUnitAccess(BaseModel):
    business_unit_id: uuid.UUID
    roles: list[str]
    clearance: Classification


class Me(BaseModel):
    user_id: uuid.UUID
    organisation_id: uuid.UUID
    display_name: str
    business_units: list[BusinessUnitAccess]


class DocumentOut(BaseModel):
    id: uuid.UUID
    business_unit_id: uuid.UUID
    title: str
    classification: Classification
    version_id: uuid.UUID
    version_number: int

    @classmethod
    def of(cls, doc: ReadableDocument) -> DocumentOut:
        return cls(
            id=doc.id,
            business_unit_id=doc.business_unit_id,
            title=doc.title,
            classification=doc.classification,
            version_id=doc.version_id,
            version_number=doc.version_number,
        )


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


def _encode_cursor(document_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(document_id.bytes).decode().rstrip("=")


def _decode_cursor(cursor: str) -> uuid.UUID:
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid cursor.") from None


@router.get("/me", response_model=Me)
async def me(ctx: CurrentUser) -> Me:
    return Me(
        user_id=ctx.user_id,
        organisation_id=ctx.organisation_id,
        display_name=ctx.display_name,
        business_units=[
            BusinessUnitAccess(
                business_unit_id=bu,
                roles=sorted(ctx.roles_by_bu.get(bu, frozenset())),
                clearance=clearance,
            )
            for bu, clearance in sorted(ctx.clearance_by_bu.items())
        ],
    )


@router.get("/documents", response_model=DocumentPage)
async def list_documents(
    request: Request,
    ctx: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> DocumentPage:
    """Documents the caller may read, oldest-id first. Pass ``next_cursor`` to continue."""
    after = _decode_cursor(cursor) if cursor else None
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        docs = await list_readable_documents(
            s, ctx, now=datetime.now(UTC), limit=limit + 1, after=after
        )
    page, more = docs[:limit], len(docs) > limit
    return DocumentPage(
        items=[DocumentOut.of(d) for d in page],
        next_cursor=_encode_cursor(page[-1].id) if more else None,
    )


@router.get(
    "/documents/{document_id}",
    response_model=DocumentOut,
    responses={404: {"description": "Not found or not visible to the caller"}},
)
async def get_document(request: Request, ctx: CurrentUser, document_id: uuid.UUID) -> DocumentOut:
    async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
        doc = await get_readable_document(s, ctx, document_id, now=datetime.now(UTC))
    if doc is None:
        # Same answer for "does not exist" and "not allowed", so existence is not revealed.
        raise HTTPException(status_code=404, detail="Not found.")
    return DocumentOut.of(doc)
