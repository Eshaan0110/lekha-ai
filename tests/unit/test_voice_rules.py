"""Phone call rules: reading what callers say, ticket drafts, spoken answers, phrases."""

from __future__ import annotations

import pytest

from lekha.actions import ProposalStatus, TicketCategory, TicketPriority
from lekha.speech import (
    CONSENT_ORDER,
    Intent,
    Phrase,
    classify,
    draft_ticket,
    mask_digits,
    parse_code,
    parse_hr_number,
    readback,
    say,
    spoken_answer,
    ticket_status,
)


@pytest.mark.parametrize(
    ("utterance", "intent"),
    [
        ("I want to talk to a person", Intent.HUMAN),
        ("मुझे किसी से बात करनी है", Intent.HUMAN),
        ("ನನಗೆ ಒಬ್ಬ ವ್ಯಕ್ತಿ ಬೇಕು", Intent.HUMAN),
        ("This is useless", Intent.UPSET),
        ("यह बकवास है", Intent.UPSET),
        ("Log a leak on line 3", Intent.LOG),
        ("I want to report that the gate is broken", Intent.LOG),
        ("मेरी शिकायत है कि पंखा खराब है", Intent.LOG),
        ("ದೂರು ದಾಖಲಿಸಬೇಕು", Intent.LOG),
        ("What is my ticket status?", Intent.STATUS),
        ("How many days of leave do I get?", Intent.QUESTION),
        # Ordinary questions that merely contain trigger-like words stay questions.
        ("How do I report harassment?", Intent.QUESTION),
        ("What is the status of my leave application?", Intent.QUESTION),
        ("Goodbye", Intent.GOODBYE),
    ],
)
def test_intents(utterance: str, intent: Intent) -> None:
    assert classify(utterance) is intent


def test_yes_and_no_only_count_while_confirming() -> None:
    assert classify("yes", confirming=True) is Intent.YES
    assert classify("ಹೌದು", confirming=True) is Intent.YES
    assert classify("नहीं", confirming=True) is Intent.NO
    assert classify("Is there no canteen?") is Intent.QUESTION
    # Asking for a person wins even mid-confirmation.
    assert classify("no, get me a person", confirming=True) is Intent.HUMAN


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("MG 10001", "MG-10001"),
        ("my number is 1 0 0 0 1", "MG-10001"),
        ("ನನ್ನ ಸಂಖ್ಯೆ ೧೦೦೦೧", "MG-10001"),  # Kannada digits
        ("मेरा नंबर १०००२ है", "MG-10002"),  # Devanagari digits
        ("1000", None),
        ("123456", None),
        ("I don't know", None),
    ],
)
def test_hr_numbers(utterance: str, expected: str | None) -> None:
    assert parse_hr_number(utterance) == expected


def test_codes_are_masked_for_the_transcript() -> None:
    assert mask_digits("it is 4 8 1 5 1 6") == "it is # # # # # #"
    assert mask_digits("೪೮೧೫೧೬") == "######"


def test_codes_need_exactly_six_digits_in_any_script() -> None:
    assert parse_code("4 8 1 5 1 6") == "481516"
    assert parse_code("೪೮೧೫೧೬") == "481516"
    assert parse_code("12345") is None
    assert parse_code("1234567") is None


def test_ticket_drafts_drop_the_request_words_and_guess_a_category() -> None:
    draft = draft_ticket("Log a complaint about the Line 3 compressor leaking oil")
    assert draft is not None
    assert draft.title == "The Line 3 compressor leaking oil"
    assert draft.category is TicketCategory.FACILITIES
    assert draft.priority is TicketPriority.P3

    safety = draft_ticket("report that there is smoke near the boiler")
    assert safety is not None
    assert (safety.category, safety.priority) == (TicketCategory.SAFETY, TicketPriority.P2)

    it = draft_ticket("my laptop won't start")
    assert it is not None
    assert it.category is TicketCategory.IT

    assert draft_ticket("log a complaint") is None  # nothing said about the problem

    # Found in the live Kannada call: the request word must not become the title.
    kannada = draft_ticket("ದೂರು: ಕ್ಯಾಂಟೀನ್ ಫ್ಯಾನ್ ಕೆಲಸ ಮಾಡುತ್ತಿಲ್ಲ")
    assert kannada is not None
    assert kannada.title == "ಕ್ಯಾಂಟೀನ್ ಫ್ಯಾನ್ ಕೆಲಸ ಮಾಡುತ್ತಿಲ್ಲ"
    hindi = draft_ticket("शिकायत - कैंटीन का पंखा खराब है")
    assert hindi is not None
    assert hindi.title == "कैंटीन का पंखा खराब है"


def test_long_descriptions_get_a_short_title_and_keep_the_full_text() -> None:
    long = "the conveyor belt on the second floor " * 6
    draft = draft_ticket(long)
    assert draft is not None
    assert len(draft.title) <= 80
    assert draft.title.endswith("...")
    assert draft.description == " ".join(long.split())


def test_spoken_answers_are_whole_sentences_without_citation_marks() -> None:
    answer = "You get 18 days of paid leave a year [1]. Unused days carry over [2]. " + "x " * 200
    assert spoken_answer(answer, limit=80) == (
        "You get 18 days of paid leave a year. Unused days carry over."
    )
    assert spoken_answer("ನಿಮಗೆ ೧೮ ದಿನ ರಜೆ ಸಿಗುತ್ತದೆ [1].") == "ನಿಮಗೆ ೧೮ ದಿನ ರಜೆ ಸಿಗುತ್ತದೆ."
    assert spoken_answer("word " * 100, limit=50).endswith("...")
    assert len(spoken_answer("word " * 100, limit=50)) <= 50


def test_every_phrase_exists_in_every_language() -> None:
    for language in CONSENT_ORDER:
        for phrase in Phrase:
            if phrase is not Phrase.READBACK:
                assert say(phrase, language).strip(), (language, phrase)
    assert say(Phrase.GOODBYE, "fr") == say(Phrase.GOODBYE, "en")  # English fallback


def test_readback_and_status_are_in_the_callers_language() -> None:
    draft = draft_ticket("fire alarm is beeping")
    assert draft is not None
    assert "ಸುರಕ್ಷತೆ" in readback(draft, "kn")
    assert "सुरक्षा" in readback(draft, "hi")
    assert "Fire alarm is beeping" in readback(draft, "en")
    line = ticket_status("Fan broken", ProposalStatus.EXECUTED, "SIM-0000ABCD", "hi")
    assert line == "Fan broken: टिकट SIM-0000ABCD बन गया."
