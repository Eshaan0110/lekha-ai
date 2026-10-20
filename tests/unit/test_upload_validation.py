"""File checks and the upload permission rule, without a database or object store."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime

import pytest

from lekha.auth import (
    AuthContext,
    Classification,
    RoleGrant,
    UploadDenial,
    build_auth_context,
    can_manage,
    upload_denial,
)
from lekha.memory import (
    FileKind,
    RejectionReason,
    UploadRejectedError,
    object_key,
    sanitise_filename,
    validate_upload,
)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
LIMIT = 1024 * 1024


def _docx(*, macros: bool = False, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        if macros:
            archive.writestr("word/vbaProject.bin", b"macro")
        for name, body in (extra or {}).items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_accepts_a_pdf() -> None:
    result = validate_upload("Policy.PDF", PDF, max_bytes=LIMIT)
    assert (result.kind, result.media_type, result.size_bytes) == (
        FileKind.PDF,
        "application/pdf",
        len(PDF),
    )
    assert len(result.sha256) == 32


def test_accepts_a_docx() -> None:
    assert validate_upload("memo.docx", _docx(), max_bytes=LIMIT).kind is FileKind.DOCX


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("empty.pdf", b"", RejectionReason.EMPTY),
        ("big.pdf", PDF + b"x" * LIMIT, RejectionReason.TOO_LARGE),
        ("notes.txt", b"hello", RejectionReason.UNSUPPORTED_TYPE),
        ("noext", PDF, RejectionReason.UNSUPPORTED_TYPE),
        ("evil.pdf", b"MZ\x90\x00 a Windows program", RejectionReason.UNSUPPORTED_TYPE),
        ("swap.docx", PDF, RejectionReason.UNSUPPORTED_TYPE),
        ("swap.pdf", _docx(), RejectionReason.UNSUPPORTED_TYPE),
        ("cut.pdf", b"%PDF-1.4\n1 0 obj", RejectionReason.MALFORMED),
        ("broken.docx", b"PK\x03\x04 not really a zip", RejectionReason.MALFORMED),
        ("plain.docx", b"PK\x03\x04", RejectionReason.MALFORMED),
        ("macro.docx", _docx(macros=True), RejectionReason.MACROS),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_rejects_unacceptable_files(name: str, data: bytes, reason: RejectionReason) -> None:
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload(name, data, max_bytes=LIMIT)
    assert excinfo.value.reason is reason


def test_rejects_a_zip_that_is_not_a_word_document() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hi")
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("fake.docx", buffer.getvalue(), max_bytes=LIMIT)
    assert excinfo.value.reason is RejectionReason.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\win.pdf", "win.pdf"),
        ("bad\x00name\x1f.pdf", "badname.pdf"),
        ("right‮to-left.pdf", "rightto-left.pdf"),  # bidi override removed
        ("", "upload"),
        ("...", "upload"),
    ],
)
def test_filenames_are_sanitised(raw: str, clean: str) -> None:
    assert sanitise_filename(raw) == clean


def test_long_filenames_keep_their_extension() -> None:
    cleaned = sanitise_filename("a" * 400 + ".docx")
    assert len(cleaned) == 255
    assert cleaned.endswith(".docx")


def test_object_keys_contain_only_ids() -> None:
    ids = [uuid.uuid4() for _ in range(4)]
    key = object_key(
        organisation_id=ids[0],
        business_unit_id=ids[1],
        document_id=ids[2],
        version_number=1,
        file_id=ids[3],
        kind=FileKind.PDF,
    )
    assert key == f"{ids[0]}/{ids[1]}/{ids[2]}/v1/{ids[3]}.pdf"


# --- who may upload --------------------------------------------------------------

NOW = datetime(2026, 10, 19, tzinfo=UTC)
ALPHA, BETA = uuid.uuid4(), uuid.uuid4()


def _ctx(*grants: tuple[uuid.UUID, str, Classification]) -> AuthContext:
    return build_auth_context(
        user_id=uuid.uuid4(),
        organisation_id=uuid.uuid4(),
        display_name="t",
        grants=[
            RoleGrant(
                business_unit_id=bu,
                role_id=uuid.uuid4(),
                role_name=name,
                max_classification=clearance,
            )
            for bu, name, clearance in grants
        ],
        now=NOW,
    )


CURATOR_ALPHA = (ALPHA, "curator", Classification.INTERNAL)
LEGAL_ALPHA = (ALPHA, "legal", Classification.CONFIDENTIAL)


@pytest.mark.parametrize(
    ("grants", "bu", "classification", "gate_open", "expected"),
    [
        ([CURATOR_ALPHA], ALPHA, Classification.INTERNAL, False, None),
        ([CURATOR_ALPHA], ALPHA, Classification.PUBLIC, False, None),
        ([CURATOR_ALPHA], BETA, Classification.INTERNAL, False, UploadDenial.NOT_CURATOR),
        (
            [(ALPHA, "viewer", Classification.INTERNAL)],
            ALPHA,
            Classification.INTERNAL,
            False,
            UploadDenial.NOT_CURATOR,
        ),
        # The gate wins even for someone cleared for confidential material.
        (
            [CURATOR_ALPHA, LEGAL_ALPHA],
            ALPHA,
            Classification.CONFIDENTIAL,
            False,
            UploadDenial.RELEASE_GATE,
        ),
        ([CURATOR_ALPHA, LEGAL_ALPHA], ALPHA, Classification.CONFIDENTIAL, True, None),
        ([CURATOR_ALPHA], ALPHA, Classification.CONFIDENTIAL, True, UploadDenial.ABOVE_CLEARANCE),
        (
            [CURATOR_ALPHA, LEGAL_ALPHA],
            ALPHA,
            Classification.RESTRICTED,
            True,
            UploadDenial.ABOVE_CLEARANCE,
        ),
        ([], ALPHA, Classification.PUBLIC, False, UploadDenial.NOT_CURATOR),
    ],
)
def test_upload_rule(
    grants: list[tuple[uuid.UUID, str, Classification]],
    bu: uuid.UUID,
    classification: Classification,
    gate_open: bool,
    expected: UploadDenial | None,
) -> None:
    decision = upload_denial(
        _ctx(*grants),
        business_unit_id=bu,
        classification=classification,
        allow_confidential=gate_open,
    )
    assert decision is expected


@pytest.mark.parametrize(
    ("grants", "bu", "classification", "expected"),
    [
        ([CURATOR_ALPHA], ALPHA, Classification.INTERNAL, True),
        ([CURATOR_ALPHA], BETA, Classification.INTERNAL, False),  # another unit's curator
        ([(ALPHA, "viewer", Classification.INTERNAL)], ALPHA, Classification.INTERNAL, False),
        ([CURATOR_ALPHA], ALPHA, Classification.CONFIDENTIAL, False),  # above clearance
        ([CURATOR_ALPHA, LEGAL_ALPHA], ALPHA, Classification.CONFIDENTIAL, True),
        ([], ALPHA, Classification.PUBLIC, False),
    ],
)
def test_who_may_change_or_delete_a_document(
    grants: list[tuple[uuid.UUID, str, Classification]],
    bu: uuid.UUID,
    classification: Classification,
    expected: bool,
) -> None:
    assert can_manage(_ctx(*grants), business_unit_id=bu, classification=classification) is expected
