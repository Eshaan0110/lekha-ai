"""Upload validation: is this file what it claims to be, and is it acceptable?

Pure Python: works on bytes in memory and never writes to disk or the network. The file's
type is decided from its *contents* (magic bytes and structure), never from the name or the
Content-Type the client sent, and the name's extension must agree with the contents.
"""

from __future__ import annotations

import hashlib
import io
import unicodedata
import zipfile
from dataclasses import dataclass
from enum import StrEnum


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    # A meeting transcript in Lekha's canonical JSON form (lekha.ingestion.transcript).
    # Only the meetings endpoint creates these; document uploads never accept them.
    TRANSCRIPT = "transcript"


MEDIA_TYPES: dict[FileKind, str] = {
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.TRANSCRIPT: "application/vnd.lekha.transcript+json",
}
_EXTENSIONS: dict[str, FileKind] = {".pdf": FileKind.PDF, ".docx": FileKind.DOCX}

MAX_FILENAME_LENGTH = 255
# DOCX is a ZIP archive. These bounds reject archives that are malformed or built to
# exhaust resources when they are extracted later (Phase 2).
MAX_DOCX_ENTRIES = 5_000
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class RejectionReason(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    MALFORMED = "malformed"
    MACROS = "macros"


class UploadRejectedError(ValueError):
    """The file is not acceptable. ``message`` is safe to show to the uploader."""

    def __init__(self, reason: RejectionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    kind: FileKind
    media_type: str
    size_bytes: int
    sha256: bytes
    filename: str  # sanitised for storage and display; never used as a storage key


def sanitise_filename(name: str) -> str:
    """Keep only the final path component, normalised, without control characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip(" .")
    if len(base) > MAX_FILENAME_LENGTH:
        stem, dot, ext = base.rpartition(".")
        keep = MAX_FILENAME_LENGTH - len(ext) - 1
        base = f"{stem[:keep]}.{ext}" if dot and keep > 0 else base[:MAX_FILENAME_LENGTH]
    return base or "upload"


def _kind_from_extension(filename: str) -> FileKind | None:
    _, dot, ext = filename.lower().rpartition(".")
    return _EXTENSIONS.get(f".{ext}") if dot else None


def _check_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise UploadRejectedError(RejectionReason.UNSUPPORTED_TYPE, "Not a PDF file.")
    # A complete PDF ends with an %%EOF marker (allowing for trailing whitespace).
    if b"%%EOF" not in data[-1024:]:
        raise UploadRejectedError(RejectionReason.MALFORMED, "The PDF appears truncated.")


def _check_docx(data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadRejectedError(RejectionReason.UNSUPPORTED_TYPE, "Not a Word (.docx) file.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        raise UploadRejectedError(RejectionReason.MALFORMED, "The Word file is damaged.") from None
    names = {entry.filename for entry in entries}
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise UploadRejectedError(RejectionReason.UNSUPPORTED_TYPE, "Not a Word (.docx) file.")
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise UploadRejectedError(
            RejectionReason.MACROS, "Word files with macros are not accepted."
        )
    if (
        len(entries) > MAX_DOCX_ENTRIES
        or sum(entry.file_size for entry in entries) > MAX_DOCX_UNCOMPRESSED_BYTES
    ):
        raise UploadRejectedError(RejectionReason.MALFORMED, "The Word file is too complex.")


_CHECKS = {FileKind.PDF: _check_pdf, FileKind.DOCX: _check_docx}


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> ValidatedFile:
    """Accept a PDF or DOCX whose contents match its extension; reject anything else."""
    if not data:
        raise UploadRejectedError(RejectionReason.EMPTY, "The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejectedError(
            RejectionReason.TOO_LARGE, f"The file is larger than {max_bytes} bytes."
        )
    safe_name = sanitise_filename(filename)
    claimed = _kind_from_extension(safe_name)
    if claimed is None:
        raise UploadRejectedError(
            RejectionReason.UNSUPPORTED_TYPE, "Only PDF (.pdf) and Word (.docx) files are accepted."
        )
    _CHECKS[claimed](data)
    return ValidatedFile(
        kind=claimed,
        media_type=MEDIA_TYPES[claimed],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).digest(),
        filename=safe_name,
    )
