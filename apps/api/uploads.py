"""POST /api/v1/documents: upload a new document (multipart: ``file`` + ``metadata`` JSON).

Order of checks, cheapest and safest first:
1. Size from Content-Length, before the body is read.
2. Metadata shape (Pydantic).
3. File contents (type sniffing, size, structure): ``lekha.memory.validate_upload``.
4. Permission to upload: ``lekha.auth.upload_denial`` (release gate, curator, clearance).
5. Guest-list badges exist and the canonical key is free.
6. Store the bytes, then write the database rows in the same tenant transaction. If the
   database write fails, the stored object is deleted again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import UploadFile

from apps.api.auth import CurrentUser, require_auth_context
from apps.api.idempotency import fingerprint, idempotency_key, response_for_claim
from db.documents import (
    NewDocument,
    canonical_key_taken,
    insert_uploaded_document,
    resolve_subject_ids,
    upload_source_id,
)
from db.idempotency import IdempotencyStore, KeyScope
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext, Classification, UploadDenial, upload_denial
from lekha.memory import (
    ObjectStore,
    ObjectStoreError,
    RejectionReason,
    UploadRejectedError,
    ValidatedFile,
    object_key,
    validate_upload,
)

logger = structlog.get_logger("lekha.uploads")

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_auth_context)],
    responses={401: {"description": "Not signed in"}},
)

# Room for the multipart boundaries and the metadata field around the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_METADATA_BYTES = 16 * 1024
MAX_ACL_SUBJECTS = 50
UPLOAD_OPERATION = "POST /api/v1/documents"

_REJECTION_STATUS = {
    RejectionReason.TOO_LARGE: 413,
    RejectionReason.UNSUPPORTED_TYPE: 415,
    RejectionReason.MACROS: 415,
    RejectionReason.EMPTY: 422,
    RejectionReason.MALFORMED: 422,
}
_DENIAL_MESSAGE = {
    UploadDenial.RELEASE_GATE: (
        "Confidential and restricted documents cannot be uploaded until the release gate is passed."
    ),
    UploadDenial.NOT_CURATOR: "You may not upload documents to this business unit.",
    UploadDenial.ABOVE_CLEARANCE: "You may not upload documents at this classification.",
}


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    canonical_key: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]{0,254}$")
    business_unit_id: uuid.UUID
    classification: Classification
    acl_subjects: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1, max_length=MAX_ACL_SUBJECTS
    )


class StoredFile(BaseModel):
    filename: str
    media_type: str
    size_bytes: int
    sha256: str


class UploadResult(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    file: StoredFile
    ingestion_job_id: uuid.UUID | None = Field(
        default=None, description="Always null until ingestion jobs exist (Phase 2)."
    )


def _enforce_declared_size(request: Request, max_bytes: int) -> None:
    declared = request.headers.get("content-length")
    if declared is None:
        raise HTTPException(status_code=411, detail="Content-Length is required.")
    if not declared.isdigit() or int(declared) > max_bytes + MULTIPART_OVERHEAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Uploads are limited to {max_bytes} bytes.")


def _parse_metadata(raw: object) -> UploadMetadata:
    if not isinstance(raw, str) or len(raw) > MAX_METADATA_BYTES:
        raise HTTPException(status_code=422, detail="A 'metadata' JSON field is required.")
    try:
        return UploadMetadata.model_validate_json(raw)
    except ValidationError as exc:
        errors = [{**e, "loc": ("metadata", *e["loc"])} for e in exc.errors()]
        raise RequestValidationError(errors) from None


@router.post(
    "/documents",
    status_code=201,
    response_model=UploadResult,
    responses={
        403: {"description": "Not allowed to upload here, or blocked by the release gate"},
        400: {"description": "Malformed Idempotency-Key header"},
        409: {
            "description": "canonical_key already used, or Idempotency-Key reused for a "
            "different request or still in progress"
        },
        411: {"description": "Content-Length header missing"},
        413: {"description": "File too large"},
        415: {"description": "Not a PDF or DOCX, or a DOCX with macros"},
        503: {"description": "Object storage unavailable"},
    },
)
async def upload_document(request: Request, ctx: CurrentUser) -> JSONResponse:
    settings = request.app.state.settings
    max_bytes: int = settings.upload_max_bytes
    _enforce_declared_size(request, max_bytes)
    idem_key = idempotency_key(request)

    # The context manager closes the spooled temporary file, even when validation fails.
    async with request.form(max_files=1, max_fields=1, max_part_size=MAX_METADATA_BYTES) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="A 'file' part is required.")
        metadata = _parse_metadata(form.get("metadata"))
        filename = upload.filename or ""
        data = await upload.read(max_bytes + 1)

    try:
        validated = validate_upload(filename, data, max_bytes=max_bytes)
    except UploadRejectedError as exc:
        raise HTTPException(status_code=_REJECTION_STATUS[exc.reason], detail=exc.message) from None

    denial = upload_denial(
        ctx,
        business_unit_id=metadata.business_unit_id,
        classification=metadata.classification,
        allow_confidential=settings.allow_confidential_uploads,
    )
    if denial is not None:
        logger.info("upload_denied", reason=denial.value)
        raise HTTPException(status_code=403, detail=_DENIAL_MESSAGE[denial])

    idempotency: IdempotencyStore = request.app.state.idempotency
    scope = None
    if idem_key is not None:
        scope = KeyScope(ctx.organisation_id, ctx.user_id, idem_key)
        claim = await idempotency.claim(
            scope,
            operation=UPLOAD_OPERATION,
            fingerprint=fingerprint(
                [
                    UPLOAD_OPERATION.encode(),
                    metadata.model_dump_json().encode(),
                    validated.sha256,
                    validated.filename.encode(),
                ]
            ),
            now=datetime.now(UTC),
        )
        replay = response_for_claim(claim)
        if replay is not None:
            if claim.body is not None:
                _set_audit_target(request, str(claim.body["document_id"]))
            return replay

    try:
        result = await _create_document(request, ctx, metadata, validated, data)
    except BaseException:
        if scope is not None:
            await idempotency.release(scope)  # nothing was created: the key may be reused
        raise

    body = result.model_dump(mode="json")
    location = f"/api/v1/documents/{result.document_id}"
    if scope is not None:
        await idempotency.complete(scope, status=201, body=body, location=location)
    # Only now does the document exist, so only now is it named in the audit event.
    _set_audit_target(request, str(result.document_id))
    return JSONResponse(status_code=201, content=body, headers={"Location": location})


def _set_audit_target(request: Request, document_id: str) -> None:
    request.scope.setdefault("state", {})["audit_target"] = ("document", document_id)


async def _create_document(
    request: Request,
    ctx: AuthContext,
    metadata: UploadMetadata,
    validated: ValidatedFile,
    data: bytes,
) -> UploadResult:
    """Store the bytes, then write the rows; delete the stored object if the rows fail."""
    document_id, version_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = object_key(
        organisation_id=ctx.organisation_id,
        business_unit_id=metadata.business_unit_id,
        document_id=document_id,
        version_number=1,
        file_id=file_id,
        kind=validated.kind,
    )
    store: ObjectStore = request.app.state.object_store
    stored = False
    try:
        async with tenant_transaction(request.app.state.session_factory, ctx.organisation_id) as s:
            subjects = await resolve_subject_ids(s, metadata.acl_subjects)
            unknown = set(metadata.acl_subjects) - set(subjects)
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"{len(unknown)} access-list subject(s) do not exist.",
                )
            if await canonical_key_taken(s, metadata.canonical_key):
                raise HTTPException(status_code=409, detail="That canonical_key is already used.")
            source_id = await upload_source_id(s)
            if source_id is None:
                logger.error("upload_source_system_missing")
                raise HTTPException(status_code=503, detail="Uploads are not configured.")
            try:
                await store.put(key, data, media_type=validated.media_type, sha256=validated.sha256)
            except ObjectStoreError:
                logger.exception("object_store_put_failed")
                raise HTTPException(
                    status_code=503, detail="File storage is unavailable."
                ) from None
            stored = True
            new = NewDocument(
                document_id=document_id,
                version_id=version_id,
                file_id=file_id,
                organisation_id=ctx.organisation_id,
                business_unit_id=metadata.business_unit_id,
                title=metadata.title,
                canonical_key=metadata.canonical_key,
                classification=metadata.classification,
                storage_key=key,
                file=validated,
                subject_ids=sorted(set(subjects.values())),
            )
            await insert_uploaded_document(
                s, new, source_system_id=source_id, now=datetime.now(UTC)
            )
    except IntegrityError:
        # Typically a concurrent upload with the same canonical_key won the race.
        await _remove_orphan(store, key, stored)
        raise HTTPException(status_code=409, detail="That canonical_key is already used.") from None
    except BaseException:
        await _remove_orphan(store, key, stored)
        raise

    return UploadResult(
        document_id=document_id,
        version_id=version_id,
        version_number=1,
        file=StoredFile(
            filename=validated.filename,
            media_type=validated.media_type,
            size_bytes=validated.size_bytes,
            sha256=validated.sha256.hex(),
        ),
    )


async def _remove_orphan(store: ObjectStore, key: str, stored: bool) -> None:
    """Delete a stored object whose database rows were not written."""
    if not stored:
        return
    try:
        await store.delete(key)
    except ObjectStoreError:
        logger.exception("orphan_object_cleanup_failed")
