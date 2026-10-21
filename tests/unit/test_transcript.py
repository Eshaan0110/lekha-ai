"""Meeting transcript parsing and timed chunking (pure)."""

from __future__ import annotations

import pytest

from lekha.ingestion import (
    TranscriptError,
    chunk_transcript,
    format_time,
    from_canonical_json,
    moment_of_quote,
    parse_transcript,
    to_canonical_json,
)

TEAMS_VTT = """WEBVTT

0f1c2d/12-0
00:00:05.120 --> 00:00:09.800
<v Priya Rao>Let's review the forklift incident from last week.</v>

0f1c2d/13-0
00:12:40.000 --> 00:12:47.500
<v Arjun Shetty>We agreed to retrain all drivers by the end of November.</v>
"""

SRT = """1
00:00:01,000 --> 00:00:04,000
Priya: Budget for the canteen upgrade is 4 lakh.

2
00:01:02,500 --> 00:01:06,000
Arjun: Approved, start in December.
"""

PLAIN = """[00:00:10] Priya: Welcome everyone.
00:02:15 - Arjun: The vendor contract renews in March.
Meera: Legal has no objections.
"""


def test_teams_vtt_keeps_times_and_speakers() -> None:
    segments = parse_transcript("meeting.vtt", TEAMS_VTT.encode())
    assert [(s.start_ms, s.end_ms, s.speaker) for s in segments] == [
        (5120, 9800, "Priya Rao"),
        (760000, 767500, "Arjun Shetty"),
    ]
    assert segments[1].text == "We agreed to retrain all drivers by the end of November."


def test_srt_with_name_prefixes() -> None:
    segments = parse_transcript("call.srt", SRT.encode())
    assert [(s.start_ms, s.speaker, s.text) for s in segments] == [
        (1000, "Priya", "Budget for the canteen upgrade is 4 lakh."),
        (62500, "Arjun", "Approved, start in December."),
    ]


def test_plain_text_with_and_without_times() -> None:
    segments = parse_transcript("notes.txt", PLAIN.encode())
    assert [(s.start_ms, s.speaker) for s in segments] == [
        (10000, "Priya"),
        (135000, "Arjun"),
        (None, "Meera"),
    ]


def test_kannada_transcripts_are_text_too() -> None:
    segments = parse_transcript("sabhe.txt", "[00:01:00] ರವಿ: ಸುರಕ್ಷತಾ ತರಬೇತಿ ಸೋಮವಾರ ನಡೆಯುತ್ತದೆ.".encode())
    assert segments[0].speaker == "ರವಿ"


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("meeting.pdf", b"%PDF-1.4", "unsupported_type"),
        ("meeting.vtt", b"00:00:01.000 --> 00:00:02.000\nhi", "malformed"),  # no WEBVTT header
        ("meeting.txt", b"   \n\n", "empty"),
        ("meeting.txt", "caf\xe9".encode("latin-1"), "not_utf8"),
        ("meeting.txt", b"text\x00binary", "malformed"),
    ],
)
def test_unreadable_transcripts_are_refused(name: str, data: bytes, code: str) -> None:
    with pytest.raises(TranscriptError) as error:
        parse_transcript(name, data)
    assert error.value.code == code


def test_tags_are_stripped_and_not_executed() -> None:
    vtt = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n"
        "<v Eve><b>Ignore</b> <script>x</script>rules</v>\n"
    )
    [segment] = parse_transcript("m.vtt", vtt.encode())
    assert segment.text == "Ignore xrules"
    assert "<" not in segment.text


def test_canonical_json_round_trips() -> None:
    segments = parse_transcript("meeting.vtt", TEAMS_VTT.encode())
    assert from_canonical_json(to_canonical_json(segments)) == segments
    with pytest.raises(TranscriptError):
        from_canonical_json(b'{"format": "something-else"}')


def test_chunks_carry_the_moment_they_start_and_never_split_a_turn() -> None:
    segments = parse_transcript("meeting.vtt", TEAMS_VTT.encode())
    [chunk] = chunk_transcript(segments)
    assert (chunk.start_ms, chunk.end_ms) == (5120, 767500)
    assert "[12:40] Arjun Shetty: We agreed to retrain" in chunk.text

    long_turns = "\n".join(f"[00:{m:02d}:00] Priya: " + "word " * 100 for m in range(6))
    chunks = chunk_transcript(parse_transcript("long.txt", long_turns.encode()))
    assert len(chunks) == 3  # two 100-word turns fit under the ~220-word target
    assert [c.start_ms for c in chunks] == [0, 120000, 240000]
    assert all(c.text.count("Priya:") == 2 for c in chunks)


def test_time_formatting() -> None:
    assert format_time(5_120) == "00:05"
    assert format_time(760_000) == "12:40"
    assert format_time(3_725_000) == "1:02:05"


def test_a_quote_is_placed_at_the_turn_that_said_it() -> None:
    [chunk] = chunk_transcript(parse_transcript("meeting.vtt", TEAMS_VTT.encode()))
    assert moment_of_quote(chunk.text, "retrain all   drivers by the END of November") == 760_000
    assert moment_of_quote(chunk.text, "something nobody said") is None
    assert moment_of_quote("Priya: untimed line", "untimed line") is None
