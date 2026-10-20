"""Text extraction from validated PDF and DOCX files (Phase 2 plan, decision D1).

Each extractor returns sections: (heading path, text). Headings come from DOCX heading
styles; PDFs have no reliable structure, so each page becomes a section. Scanned PDFs (no
text layer) raise ``ExtractionError`` rather than returning nothing: OCR is deferred.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from lekha.memory import FileKind

MIN_EXTRACTED_CHARS = 1


class ExtractionError(ValueError):
    """The file could not be turned into text. ``code`` is safe to store and show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Section:
    heading_path: tuple[str, ...]
    text: str
    page: int | None = None


def _extract_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("encrypted")
        sections = [
            Section((), (page.extract_text() or "").strip(), page=number)
            for number, page in enumerate(reader.pages, start=1)
        ]
    except PdfReadError:
        raise ExtractionError("unreadable_pdf") from None
    return [s for s in sections if s.text]


def _extract_docx(data: bytes) -> list[Section]:
    import docx

    document = docx.Document(io.BytesIO(data))
    sections: list[Section] = []
    path: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            sections.append(Section(tuple(path), "\n".join(buffer)))
            buffer.clear()

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name if paragraph.style is not None else ""
        if style.startswith("Heading") and style[-1:].isdigit():
            flush()
            level = int(style[-1])
            path[:] = [*path[: level - 1], text]
        else:
            buffer.append(text)
    flush()
    return sections


def extract_sections(kind: FileKind, data: bytes) -> list[Section]:
    sections = _extract_pdf(data) if kind is FileKind.PDF else _extract_docx(data)
    if sum(len(s.text) for s in sections) < MIN_EXTRACTED_CHARS:
        raise ExtractionError("no_text_layer")  # e.g. a scanned PDF; OCR comes later
    return sections
