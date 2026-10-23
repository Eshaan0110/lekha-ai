"""Speech-to-text and text-to-speech interfaces (Phase 5).

Vendor adapters (Sarvam Saaras and Bulbul, per the plan) plug in here later, only with the
owner's authorisation (Phase 5 decision D2). Until then the FAKES below are the only
implementations, and they are SIMULATIONS: their "audio" is UTF-8 text in a made-up media
type, so typed words travel through the same speech path a real call would use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

FAKE_AUDIO_TYPE = "application/vnd.lekha.fake-audio"  # SIMULATION: the "audio" is text
MAX_AUDIO_BYTES = 2 * 1024 * 1024  # about a minute of 16 kHz, 16-bit mono audio


class SpeechError(RuntimeError):
    """The speech service failed or timed out. The message never contains audio or text."""


class UnsupportedAudioError(ValueError):
    """The audio's format is not one this service accepts."""


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str | None  # BCP-47 ("kn", "hi", "en"), if the service detected one
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class Audio:
    data: bytes
    media_type: str


class SpeechToText(Protocol):
    @property
    def model_name(self) -> str: ...

    async def transcribe(
        self, audio: bytes, media_type: str, *, language_hint: str | None = None
    ) -> Transcript: ...


class TextToSpeech(Protocol):
    @property
    def model_name(self) -> str: ...

    async def synthesize(self, text: str, language: str) -> Audio: ...


class FakeSpeechToText:
    """SIMULATION ONLY: "transcribes" fake audio by decoding the text inside it. The
    language is left to Lekha's own detection, as with a service that doesn't report it."""

    model_name = "fake-stt"

    async def transcribe(
        self, audio: bytes, media_type: str, *, language_hint: str | None = None
    ) -> Transcript:
        if media_type != FAKE_AUDIO_TYPE:
            raise UnsupportedAudioError(media_type)
        if len(audio) > MAX_AUDIO_BYTES:
            raise UnsupportedAudioError("too long")
        try:
            text = audio.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedAudioError("not fake audio") from None
        return Transcript(text=" ".join(text.split()), language=None, confidence=1.0)


class FakeTextToSpeech:
    """SIMULATION ONLY: "speaks" by wrapping the text as fake audio."""

    model_name = "fake-tts"

    async def synthesize(self, text: str, language: str) -> Audio:
        return Audio(data=text.encode("utf-8"), media_type=FAKE_AUDIO_TYPE)
