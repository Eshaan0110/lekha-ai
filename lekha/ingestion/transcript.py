"""Meeting transcripts: parse common export formats into timed segments, and cut them into
timed chunks for search (Phase 4, meeting memory). Pure: no I/O.

Accepted formats (what Teams, Otter, Zoom and Granola can export as text):
- WebVTT (.vtt): cue times, speaker from ``<v Name>`` voice tags or a "Name:" prefix
- SubRip (.srt): numbered cues with times
- plain text (.txt): one turn per line, optionally "[00:12:40]" and "Name:" prefixes

Transcripts are stored in one canonical JSON form, so the pipeline never re-parses the
original format. Like documents, transcript text is data, never instructions.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from lekha.ingestion.chunking import Chunk
from lekha.ingestion.language import detect_language

MAX_SEGMENTS = 20_000
MAX_SEGMENT_CHARS = 5_000
MAX_SPEAKER_CHARS = 60
CHUNK_TARGET_WORDS = 220  # smaller than document chunks, so citations point at a moment
FORMAT_VERSION = 1

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_CUE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_VOICE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]+)>")
_TAG = re.compile(r"</?[^>]+>")
_SPEAKER = re.compile(r"^([^\s:][^:]{0,58}):\s+(.+)$")
_LINE_TIME = re.compile(rf"^\s*\[?{_TIME}\]?\s*[-–—]?\s*(.*)$")  # noqa: RUF001 - dashes people type after a time


class TranscriptError(ValueError):
    """The file isn't a transcript we can read; ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Segment:
    start_ms: int | None
    end_ms: int | None
    speaker: str | None
    text: str


def _ms(hours: str | None, minutes: str, seconds: str, fraction: str | None) -> int:
    frac = int((fraction or "0").ljust(3, "0")[:3])
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + frac


def format_time(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _speaker_and_text(text: str) -> tuple[str | None, str]:
    voice = _VOICE.search(text)
    if voice:
        return voice.group(1).strip()[:MAX_SPEAKER_CHARS], _TAG.sub("", text).strip()
    clean = _TAG.sub("", text).strip()
    named = _SPEAKER.match(clean)
    if named and not named.group(1).strip().isdigit():
        return named.group(1).strip(), named.group(2).strip()
    return None, clean


def _cues(text: str) -> list[Segment]:
    """VTT and SRT: a timing line, then text lines until a blank line."""
    segments: list[Segment] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        timing = _CUE.match(lines[i])
        if not timing:
            i += 1
            continue
        g = timing.groups()
        start, end = _ms(*g[0:4]), _ms(*g[4:8])
        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip():
            body.append(lines[i].strip())
            i += 1
        speaker, spoken = _speaker_and_text(" ".join(body))
        if spoken:
            segments.append(Segment(start, max(end, start), speaker, spoken))
    return segments


def _plain(text: str) -> list[Segment]:
    segments: list[Segment] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        start: int | None = None
        timed = _LINE_TIME.match(line)
        if timed and timed.group(5):
            start = _ms(*timed.groups()[0:4])
            line = timed.group(5).strip()
        speaker, spoken = _speaker_and_text(line)
        if spoken:
            segments.append(Segment(start, None, speaker, spoken))
    return segments


def parse_transcript(filename: str, data: bytes) -> list[Segment]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TranscriptError("not_utf8", "Transcripts must be UTF-8 text.") from None
    if "\x00" in text:
        raise TranscriptError("malformed", "This doesn't look like a text transcript.")
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension not in {"vtt", "srt", "txt"}:
        raise TranscriptError("unsupported_type", "Transcripts must be .vtt, .srt or .txt files.")
    if extension == "vtt" and not text.lstrip().startswith("WEBVTT"):
        raise TranscriptError("malformed", "A .vtt file must start with WEBVTT.")
    segments = _cues(text) if extension in {"vtt", "srt"} else _plain(text)
    if not segments:
        raise TranscriptError("empty", "No spoken text was found in the transcript.")
    if len(segments) > MAX_SEGMENTS:
        raise TranscriptError("too_large", f"Transcripts are limited to {MAX_SEGMENTS} turns.")
    return [Segment(s.start_ms, s.end_ms, s.speaker, s.text[:MAX_SEGMENT_CHARS]) for s in segments]


def to_canonical_json(segments: Iterable[Segment]) -> bytes:
    body = {
        "format": "lekha-transcript",
        "version": FORMAT_VERSION,
        "segments": [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "speaker": s.speaker, "text": s.text}
            for s in segments
        ],
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def from_canonical_json(data: bytes) -> list[Segment]:
    try:
        body = json.loads(data)
        if body.get("format") != "lekha-transcript":
            raise TypeError
        return [
            Segment(s["start_ms"], s["end_ms"], s["speaker"], str(s["text"]))
            for s in body["segments"]
        ]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise TranscriptError("malformed", "Stored transcript is unreadable.") from None


def _line(segment: Segment) -> str:
    when = f"[{format_time(segment.start_ms)}] " if segment.start_ms is not None else ""
    who = f"{segment.speaker}: " if segment.speaker else ""
    return f"{when}{who}{segment.text}"


def chunk_transcript(segments: list[Segment]) -> list[Chunk]:
    """Consecutive turns grouped into chunks of about CHUNK_TARGET_WORDS words. A turn is
    never split, so every chunk starts at a real moment of the meeting."""
    chunks: list[Chunk] = []
    current: list[Segment] = []

    def flush() -> None:
        if not current:
            return
        text = "\n".join(_line(s) for s in current)
        starts = [s.start_ms for s in current if s.start_ms is not None]
        ends = [s.end_ms if s.end_ms is not None else s.start_ms for s in current]
        known_ends = [e for e in ends if e is not None]
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                text=text,
                heading_path=(),
                language=detect_language(text),
                token_count=len(text.split()),
                page=None,
                start_ms=min(starts) if starts else None,
                end_ms=max(known_ends) if known_ends else None,
            )
        )
        current.clear()

    words = 0
    for segment in segments:
        length = len(segment.text.split())
        if current and words + length > CHUNK_TARGET_WORDS:
            flush()
            words = 0
        current.append(segment)
        words += length
    flush()
    return chunks


_TURN_TIME = re.compile(r"^\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]")


def moment_of_quote(chunk_text: str, quote: str) -> int | None:
    """When in the meeting a quoted passage was said: the time of the transcript line that
    contains it. None if the quote isn't on one timed line."""
    wanted = " ".join(quote.split()).casefold()
    if not wanted:
        return None
    for line in chunk_text.splitlines():
        if wanted in " ".join(line.split()).casefold():
            timed = _TURN_TIME.match(line)
            if timed:
                return _ms(timed.group(1), timed.group(2), timed.group(3), None)
    return None
