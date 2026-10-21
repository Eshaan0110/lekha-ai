# ADR-0016: Glossary applied by expanding questions, not by relinking documents

- **Status:** Proposed (needs the owner's acceptance)
- **Date:** 2026-10-21

## Context
The plan wants a shared glossary and entity registry so that a question using one name
("MPi", "PL", "casual leave") finds content that uses another ("Manipal…",
"privilege leave", "ಸಾಂದರ್ಭಿಕ ರಜೆ").

Its pipeline does this *before* chunking (stage 8, "entity and glossary processing"): names
in documents are normalised so embeddings are stable, and a glossary change re-runs ingestion
from that stage. It also plans Clavis-seeded entities, which are blocked on Phase 0.

The Phase 3 evaluation showed the main miss was English questions about Kannada documents.

## Decision
1. **Glossary entries** (`glossary_entry`, plus `glossary_name` for every name, canonical
   included):
   - owned by a business unit and managed by its curators;
   - visible to that unit, or to the whole organisation when marked `shared`;
   - a name means one thing per organisation (unique, compared ignoring case and spaces).
2. **Applied at question time.** Names in the question are matched as whole words, also
   in Kannada. The question gains the other names of each matched entry, at most 5 entries.
3. **A matched name is a strong signal.** Besides adding the names to the meaning and keyword
   searches, a third keyword search on the glossary names alone joins the rank fusion, so
   generic words can't drown it out.
4. **The answer model is told** which names mean the same thing (curator-written text,
   neutralised like everything else).
5. **Users see it:** search and answers report "Also searched for: …".

## Alternatives considered
- **Entity linking at ingestion (the plan's stage 8):** better for embeddings, but every
  glossary edit means re-processing every affected document, and it needs entity
  extraction we don't have yet. It can be added later on top of this.
- **Synonyms in PostgreSQL full-text dictionaries:** keyword search only, server-wide
  configuration, no per-unit visibility.
- **Letting the language model guess synonyms:** not grounded, not controllable by curators.

## Why it fits Lekha
- Curators control the vocabulary.
- A change takes effect immediately with no re-indexing.
- Visibility follows the same unit and role model as everything else.
- Measured live, it fixed the evaluation's cross-language misses (casual leave, medical
  check-up).

## Trade-offs
- Expansion helps only questions that use a known name; documents aren't normalised.
- More names mean longer queries; capped at 5 entries.
- Bad entries (a too-generic alias) could widen searches. They can't widen *permissions*,
  since every search is still permission-filtered, but curators should review entries.

## Security and operational implications
- An entry's names are visible to the people who can see the entry. Unshared entries stay
  within their unit.
- Expansion never changes who can read what.
- Entry text sent to the model is neutralised so it can't fake a source fence.

## Revisit when
- Clavis entities become available (seeding).
- The golden set shows document-side entity linking would add recall.
- Glossaries grow large enough that matching needs an index (today it is a scan of the
  visible entries).
