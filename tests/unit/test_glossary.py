"""Glossary matching and query expansion (pure)."""

from __future__ import annotations

import uuid

from lekha.memory import GlossaryEntry, expand_query, find_expansions, glossary_note, normalise_term

LEAVE = GlossaryEntry(uuid.uuid4(), "Annual leave", ("PL", "privilege leave", "EL"), "paid holiday")
CASUAL = GlossaryEntry(uuid.uuid4(), "Casual leave", ("CL", "ಸಾಂದರ್ಭಿಕ ರಜೆ"))
MPL = GlossaryEntry(uuid.uuid4(), "Manipal Pharma Labs", ("MPL",))
ENTRIES = [LEAVE, CASUAL, MPL]


def test_whole_words_only_ignoring_case_and_spacing() -> None:
    assert [e.entry for e in find_expansions("How many PL do I get?", ENTRIES)] == [LEAVE]
    assert find_expansions("Is the PLANT open?", ENTRIES) == []  # "PL" inside a word
    assert [e.matched for e in find_expansions("privilege   LEAVE rules", ENTRIES)] == [
        "privilege leave"
    ]


def test_kannada_names_match_as_whole_words() -> None:
    [found] = find_expansions("ಸಾಂದರ್ಭಿಕ ರಜೆ ಎಷ್ಟು ದಿನ?", ENTRIES)
    assert found.entry == CASUAL
    assert find_expansions("ಸಾಂದರ್ಭಿಕರಜೆ", ENTRIES) == []  # no word edge


def test_the_query_gains_the_other_names_once() -> None:
    expansions = find_expansions("How many days of casual leave per year?", ENTRIES)
    assert expand_query("How many days of casual leave per year?", expansions) == (
        "How many days of casual leave per year? (CL; ಸಾಂದರ್ಭಿಕ ರಜೆ)"
    )
    assert expand_query("no glossary words here", []) == "no glossary words here"


def test_several_entries_longest_match_first() -> None:
    found = find_expansions("Does MPL give privilege leave?", ENTRIES)
    assert [e.entry.canonical for e in found] == ["Annual leave", "Manipal Pharma Labs"]


def test_the_model_is_told_which_names_mean_the_same() -> None:
    note = glossary_note(find_expansions("PL at MPL", ENTRIES))
    assert "- Annual leave = PL = privilege leave = EL (paid holiday)" in note
    assert "- Manipal Pharma Labs = MPL" in note


def test_normalisation() -> None:
    assert normalise_term("  Privilege\tLeave ") == "privilege leave"
