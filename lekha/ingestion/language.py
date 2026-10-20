"""Language detection by script (Phase 2 plan, decision D2).

English, Hindi and Kannada are written in different scripts (Latin, Devanagari, Kannada),
so counting letters per script identifies them exactly with no model download. Known limit:
Hindi typed in Latin letters is reported as English. Returns BCP-47 codes.
"""

from __future__ import annotations

from enum import StrEnum

_DEVANAGARI = range(0x0900, 0x0980)
_KANNADA = range(0x0C80, 0x0D00)


class Language(StrEnum):
    ENGLISH = "en"
    HINDI = "hi"
    KANNADA = "kn"
    UNKNOWN = "und"


def detect_language(text: str) -> Language:
    counts = {Language.ENGLISH: 0, Language.HINDI: 0, Language.KANNADA: 0}
    for char in text:
        code = ord(char)
        if code in _DEVANAGARI:
            counts[Language.HINDI] += 1
        elif code in _KANNADA:
            counts[Language.KANNADA] += 1
        elif char.isascii() and char.isalpha():
            counts[Language.ENGLISH] += 1
    best = max(counts, key=lambda lang: counts[lang])
    return best if counts[best] > 0 else Language.UNKNOWN
