"""Speech interfaces (the labelled fakes) and glossary-aware name correction."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from lekha.core.config import Environment, Settings
from lekha.speech import correct_names
from model_layer import FAKE_AUDIO_TYPE, FakeSpeechToText, FakeTextToSpeech, UnsupportedAudioError

NAMES = ["MPL", "Manipal Pharma Labs", "PL", "privilege leave", "ಸಾಂದರ್ಭಿಕ ರಜೆ", "casual leave"]


@pytest.mark.parametrize(
    ("heard", "meant"),
    [
        ("How many M P L shares are there?", "How many MPL shares are there?"),
        ("is m.p.l. open today", "is MPL open today"),
        ("Is Manipal Farma Labs open?", "Is Manipal Pharma Labs open?"),
        ("how many privelege leave days", "how many privilege leave days"),
        ("ಸಾಂದರ್ಬಿಕ ರಜೆ ಎಷ್ಟು ದಿನ?", "ಸಾಂದರ್ಭಿಕ ರಜೆ ಎಷ್ಟು ದಿನ?"),  # one wrong letter in Kannada
    ],
)
def test_near_misses_of_glossary_names_are_corrected(heard: str, meant: str) -> None:
    corrected, changes = correct_names(heard, NAMES)
    assert corrected == meant
    assert len(changes) == 1


@pytest.mark.parametrize(
    "text",
    [
        "planned PLANT visit",  # short names only match exactly
        "How many PL do I get",  # already right
        "Manipal Pharma Labs canteen",
        "I need help with my payslip",
        "",
    ],
)
def test_everything_else_is_left_alone(text: str) -> None:
    assert correct_names(text, NAMES) == (text, [])


def test_no_names_means_no_change() -> None:
    assert correct_names("M P L", []) == ("M P L", [])


async def test_the_fake_speech_round_trip_is_plain_text() -> None:
    spoken = await FakeTextToSpeech().synthesize("ನಮಸ್ಕಾರ", "kn")
    assert spoken.media_type == FAKE_AUDIO_TYPE
    heard = await FakeSpeechToText().transcribe(spoken.data, spoken.media_type)
    assert heard.text == "ನಮಸ್ಕಾರ"
    assert heard.language is None  # left to Lekha's own detection


async def test_the_fake_refuses_anything_that_is_not_fake_audio() -> None:
    with pytest.raises(UnsupportedAudioError):
        await FakeSpeechToText().transcribe(b"RIFF....WAVE", "audio/wav")
    with pytest.raises(UnsupportedAudioError):
        await FakeSpeechToText().transcribe(b"\xff\xfe", FAKE_AUDIO_TYPE)


def test_fake_speech_is_refused_outside_local_and_test(
    make_settings: Callable[..., Settings],
) -> None:
    assert make_settings(speech="fake").speech == "fake"
    with pytest.raises(ValidationError, match="simulation"):
        make_settings(environment=Environment.STAGING, speech="fake")
