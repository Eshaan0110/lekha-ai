# ADR-0014: Hybrid retrieval (meaning + keywords) with reciprocal rank fusion

- **Status:** Accepted by the owner on 2026-10-21 (Phase 3 plan decision D2; implements the
  plan's ADR-008)
- **Date:** 2026-10-21

## Context
Meaning search with BGE-M3 is weak at exact identifiers. On the Phase 2 corpus it found
only 108 of 124 documents (87%) when searching by reference code. The plan's ADR-008
prescribes dense + BM25 with reciprocal rank fusion, and asks that each retrieval addition
be justified by before/after evidence.

## Decision
1. **Keyword search in PostgreSQL:**
   - a generated `tsvector` column on `chunk` with the `simple` configuration (lower-cased
     words, no stemming, no stop words), so English and Kannada are treated alike;
   - a GIN index;
   - any question word may match, ranked by `ts_rank_cd`.
2. **The same access rule in SQL** (`readable_condition`) is applied inside the keyword
   query, so keyword search can't leak.
3. **Reciprocal rank fusion (k=60)** merges the dense and keyword lists; the database
   re-check still runs on the result.
4. `LEKHA_RETRIEVAL_MODE=dense|hybrid` (default `hybrid`) keeps the before/after comparison
   possible.

## Alternatives considered
- **Qdrant sparse vectors** (BGE-M3's sparse output): one store for both halves, but more
  model work, and the plan places BM25 in PostgreSQL.
- **A separate search engine** (OpenSearch): another service to run, which a modular
  monolith doesn't need yet.
- **Dense only plus a reranker:** a reranker can't recover documents that retrieval never
  returned.

## Why it fits Lekha
Policy codes, case numbers and names matter in enterprise questions. The permission rule
stays in one place (SQL), and no new infrastructure is added.

## Trade-offs
- `simple` has no stemming, so "leaves" won't match "leave". It is acceptable alongside
  dense search.
- Very common words match many chunks; fusion and ranking absorb that.
- One extra query per search (milliseconds at our scale).

## Security and operational implications
- Question words are quoted before being built into a `tsquery`, so search syntax in a
  question can't change the query (tested).
- The index is maintained by PostgreSQL automatically.
- Backups already cover it.

## Cost
None beyond disk for the index.

## Revisit when
- The golden set shows keyword search adds nothing (the plan's reversal condition).
- Volumes grow beyond what PostgreSQL full-text search handles comfortably.

**Evidence (2026-10-21):** BGE-M3 exact-code recall went from 87% (dense) to 100% (hybrid)
on the Phase 2 corpus, and the permission red team still shows 0 anomalies.
