# Phase 3 — Grounded Q&A: end-of-phase report

- **Date:** 2026-10-21
- **Scope:** local implementation on the owner's laptop, **synthetic documents only**
  (owner's GO of 2026-10-21). Hindi deferred.
- **Plan:** [design/phase-3-plan.md](../design/phase-3-plan.md), approved with D1–D6.
- **Code state:** branch `phase-2/prep`, 7 commits since the Phase 2 report (`6c301f8` …
  this report). Committed locally; **not pushed** (push when the owner says).
- **Evaluation report:** [eval/golden-ollama-bge-m3.md](../eval/golden-ollama-bge-m3.md)

---

## A. What we built

Lekha can now **answer questions**, not just find documents. It answers only from documents
the asker may read, cites the exact words it relied on, and says "I don't know" rather than
guess.

1. **`POST /api/v1/ask`:** you ask in plain English or Kannada and get back either:
   - an answer with numbered citations, each naming the document, its version and the
     exact quoted sentence; or
   - "I do not have enough information to answer this", with the reason.
2. **A pluggable "model layer":**
   - the answer-writing AI (the LLM, "large language model") sits behind one interface;
   - locally it is **Qwen 3.5 (9B) running in Ollama on this laptop**, so no text leaves the
     machine;
   - the production choice, **Claude on AWS Bedrock**, is written and tested against a
     stand-in AWS client, ready for when the AWS account exists.
3. **Safety rules around the AI:**
   - it only ever sees chunks the asker may read;
   - documents are fenced off as "data, not instructions";
   - every quote it cites is checked word for word against the source, and any that fail
     are dropped;
   - if nothing survives, Lekha abstains.
4. **Hybrid search:** keyword search (exact words and codes) now runs alongside meaning
   search, with the same permission rule. Exact-code lookups went from 87% to 100%.
5. **Logs:**
   - every question, answer and citation is recorded in the database, protected per
     company;
   - when a document is deleted, stored answers that quoted it are wiped;
   - `/ask` is rate-limited per user.
6. **A 100-question golden test set** with a hand-written answer key, and a harness that
   scores the plan's quality scoreboard.

## B. How the system works

```mermaid
sequenceDiagram
    participant U as Employee
    participant API as /api/v1/ask
    participant RL as Rate limit (Redis)
    participant PG as PostgreSQL
    participant R as Retrieval
    participant LLM as Language model
    U->>API: question
    API->>RL: per-user count (fail closed)
    API->>PG: query_log row
    API->>R: hybrid search WITH permission filter
    R-->>API: up to 8 readable chunks + best relevance
    alt none / weak evidence
        API-->>U: "I do not have enough information" (model never called)
    else enough evidence
        API->>LLM: fixed prompt, sources fenced as data, JSON schema
        LLM-->>API: {answer, citations[{source, span}], abstained}
        API->>API: verify every span is in its source; drop the rest
        API->>PG: answer_log + citation rows
        API-->>U: answer + verified citations (or abstention)
    end
```

**New words:**
- **LLM (large language model):** the AI that writes sentences. It is clever but can make
  things up, so we never let it answer without checking its quotes.
- **RAG (retrieval-augmented generation):** find the right passages first, then ask the AI
  to answer *only* from them. Like an open-book exam where the examiner checks every quote.
- **Citation verification:** the AI must copy the exact words it relied on. If those words
  aren't in the cited passage, the citation is thrown away.
- **Abstention:** answering "I don't know". It is a feature: a wrong policy answer is worse
  than none.
- **Hybrid retrieval:** two searches, one by meaning and one by exact words, whose result
  lists are merged by rank (reciprocal rank fusion).
- **Golden set:** a fixed exam (questions plus a hand-written answer key) used to measure
  quality the same way every time.

## C. Technology decisions

| Technology | What and why | Alternatives / trade-offs | Where |
|---|---|---|---|
| **Ollama + qwen3.5:9b** (local LLM) | Runs the answer model on the laptop. It was already downloaded, so no paid API and no data leaving the machine | Claude via the Anthropic API is faster and better, but paid and not authorised. Trade-off: 18–29 s per answer here, weaker Kannada | `model_layer/llm.py` (`OllamaModel`) |
| **Claude via AWS Bedrock** | The plan's production model (Mumbai region) | Untested live: blocked on the AWS account. Contract-tested with botocore Stubber | `model_layer/llm.py` (`BedrockClaudeModel`) |
| **Fixed prompt with fenced sources** | Stops documents posing as instructions | Prompting alone can't guarantee obedience, so it is backed by the verifier, permission filtering and no tools | `lekha/generation/prompt.py` |
| **Span verifier** | Proves every cited quote exists in its source | It can't prove truth: a verbatim quote of injected text would verify | `lekha/generation/answer.py` |
| **PostgreSQL full-text search** | The keyword half of hybrid search, with the access rule in the same SQL | No stemming (`simple`), but it works for Kannada. No extra service | `db/search.py`, migration 0007 |
| **Reciprocal rank fusion** | Merges lists whose scores aren't comparable | Simple and robust; a reranker could reorder better later | `lekha/retrieval/fusion.py` |
| **Redis rate limit** | Caps `/ask` per user per minute | A fixed window allows short bursts at window edges | `apps/api/ratelimit.py` |

Recorded as **ADR-0013** (language models and grounding) and **ADR-0014** (hybrid
retrieval), both accepted through the plan approval.

## D. Architecture

- **New modules:**
  - `lekha/generation` is **pure**: no database, network or model vendor. A new import
    contract (now 7, all kept) enforces it;
  - `model_layer/llm.py` doesn't know about Lekha;
  - `apps/api/ask.py` orchestrates;
  - `apps/api/retrieval.py` is shared by `/search` and `/ask`.
- **Trust boundaries:**
  - user → API: validated, rate-limited;
  - retrieved text → model: fenced, never instructions;
  - model → user: its output is untrusted until parsed and verified.
- **Defence in depth against leaks:** the permission filter in Qdrant, the access rule in
  SQL for keyword search, the database re-check, then only those chunks reach the model.
  A test records every prompt and proves restricted text never reached it.
- **Patterns:** ports and adapters for models; a deterministic fake for tests; fail-closed
  gates.

## E. Files created or changed

**Model, generation and API**

| File | Purpose |
|---|---|
| `model_layer/llm.py` | `LanguageModel` interface, `OllamaModel`, `BedrockClaudeModel` |
| `lekha/generation/prompt.py`, `answer.py` | Fixed prompt and fencing; JSON parsing, span verification, abstention reasons |
| `lekha/retrieval/fusion.py`, `db/search.py` | Reciprocal rank fusion; permission-filtered keyword search |
| `apps/api/retrieval.py` | Shared hybrid retrieval with the database re-check |
| `apps/api/ask.py`, `apps/api/ratelimit.py` | `/api/v1/ask`; per-user limit |

**Database, workers and config**

| File | Purpose |
|---|---|
| `db/answers.py`, `db/schema/answers.py` | `query_log`, `answer_log`, `citation`; redaction on deletion |
| Migrations `0007`, `0008` | Full-text column and index; log tables with RLS |
| `apps/worker/purge.py` | Deletion now also wipes answers that quoted the document |
| `lekha/core/config.py` | `LEKHA_LLM`, Ollama and Bedrock settings, ask settings, retrieval mode, and guards: Ollama must be local and not a cloud model, and is refused in production |

**Tests and docs**

| File | Purpose |
|---|---|
| `tests/fixtures/fake_llm.py` | Deterministic fake model that records prompts |
| `tests/eval/golden.py`, `tests/eval/harness.py`, `tests/integration/test_golden.py` | Golden set, scoring, end-to-end run |
| `tests/unit/test_llm_adapters.py`, `test_generation.py`, `test_fusion.py`, `test_eval_harness.py`; `tests/integration/test_ask.py` | New tests |
| `dev.ps1` | `eval` command |
| `docs/adr/0013`, `0014`; `docs/eval/golden-ollama-bge-m3.md`; tracker, security, architecture, local-dev | Docs |

## F. Testing and verification

Commands run on 2026-10-21 (final run after the last code change):

| Command | Result |
|---|---|
| `uv run ruff format --check .` | 159 files formatted |
| `uv run ruff check .` | All checks passed |
| `uv run mypy .` | No issues in 130 source files |
| `uv run lint-imports` | 7 contracts kept |
| `uv lock --check` | OK |
| `uv run pytest -m "not integration"` | **218 passed** |
| `uv run pytest -m integration` | **110 passed** (5 min 51 s), including the Phase 2 red team with hybrid retrieval and the golden set with the fake model |
| Golden set, real: `LEKHA_EVAL_EMBEDDER=bge-m3 LEKHA_EVAL_LLM=ollama … test_golden.py` | **Passed** in 27 min 36 s; report below |

**Golden set, BGE-M3 + qwen3.5:9b, hybrid retrieval, 8 sources per question:**

| Metric | Result | P0 target |
|---|---|---|
| Recall@10 | 0.90 | ≥ 0.80 |
| Answer accuracy (answered questions) | 1.00 (63 of 63) | ≥ 0.90 |
| Citation correctness | 1.00 (64 of 64 citations) | ≥ 0.90 |
| Faithfulness (numbers in answer appear in the cited text) | 1.00 | ≥ 0.90 |
| Abstention correctness (unanswerable) | 1.00 (20 of 20) | ≥ 0.85 |
| Over-abstention (answerable but "I don't know") | 0.10 (7 of 70) | — |
| Permission violations | **0** | 0 |
| Injection failures (10 probes) | **0** | 0 |
| Latency p50 / p95 | 18.1 s / 28.8 s | p95 ≤ 6 s |

**How to read these numbers honestly:**
- **The test set is small and synthetic**, and I wrote both the documents and the
  questions. Real policies are longer and messier, so expect lower scores on real data.
- **The relevance cut-off (0.50) was tuned on this same set,** which flatters abstention.
  It needs a separate validation set.
- **The 7 over-abstentions:**
  - 5 are **English questions about Kannada documents** (K12, K32, K42, K52, K62);
  - 2 are English questions whose best match fell just under the cut-off (A08, A14);
  - cross-language answering is the weakest area.
- **Injection: 0 failures on 10 probes** is encouraging but a small sample. With the fake
  extractive model, 1 probe "failed", because it quoted the injected sentence. That shows
  verification alone doesn't stop injection; the real model followed the fenced
  instructions.

## G. Security and operations review

**Implemented and tested:**

- **Authorisation before generation:**
  - the model sees only permission-filtered chunks (`test_the_model_is_never_shown_text_the_asker_may_not_read`
    records every prompt);
  - the Phase 2 red team still passes with hybrid retrieval: 350 searches, 0 anomalies;
  - the golden probes show 0 violations.
- **No fabricated citations:** every returned citation's quote is in its source, and
  unsupported markers are stripped (`test_generation.py`, `test_ask.py`).
- **Abstention paths:**
  - no sources → abstain, and the model is never called;
  - weak evidence → abstain, and the model is never called;
  - the model abstains, or its reply can't be parsed → abstain;
  - every citation fails verification → abstain.
- **Prompt injection:** sources are fenced, fence-like text is neutralised (tested), the
  model has no tools or actions, and the golden probes show 0 failures.
- **Data stays local:** the Ollama address must be loopback, cloud model tags are refused,
  and Ollama is refused in staging and production (`test_config.py`).
- **Fail closed:**
  - model down → 503 `upstream_unavailable`;
  - no model configured → 503;
  - rate-limit store down → 503;
  - over the limit → 429 with `Retry-After`.
- **Logging and privacy:**
  - question and answer text go to RLS-protected tables, never to application logs (logs
    record only counts, reasons and timings);
  - deleting a document wipes stored answers that quoted it, and their citations.
- **Audit:** every `/ask` request writes an audit event that names its `query_log` row.

**Placeholders and production prerequisites:**
- Bedrock is not run live (AWS account, Phase 0).
- The p95 ≤ 6 s target is unmet on the laptop.
- Retention of query and answer logs (personal-data rules) is not built.
- No `audit_reader` view of the logs yet.
- No cost ceiling per model call.
- No monitoring (Langfuse, Prometheus).
- Group OIDC is blocked.
- The release gate for real data is blocked.

## H. Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| Permission-filtered retrieval feeding generation | **PASS** | `test_ask.py` (prompt capture), red team 0 anomalies |
| Pluggable model layer, providers interchangeable | **PASS** (local) | One interface; Ollama live; Bedrock contract-tested; fake for tests |
| Answer generation with inline citations | **PASS** | `/api/v1/ask`; golden set |
| Citation verification; no fabricated citations | **PASS** | Span verifier; citation correctness 1.00 |
| Abstention when evidence is insufficient (≥ 0.85) | **PASS** (synthetic) | 1.00 on 20 unanswerable; floor tuned on the same set |
| Citation correctness ≥ 0.90 on a 100-question golden set | **PASS** (synthetic) | 1.00 |
| Answer faithfulness ≥ 0.90 | **PASS** (synthetic, rule proxy) | 1.00 |
| Retrieval Recall@10 ≥ 0.80 | **PASS** | 0.90 |
| 0 permission leaks | **PASS** | 0 in the golden set (fake and real models), the red team and `/ask` tests |
| Prompt injection has no effect | **PASS** (small sample) | 0 of 10 probes; fencing tests |
| p95 answer ≤ 6 s | **FAIL locally / BLOCKED for production** | 28.8 s on the laptop with a 9B local model. Must be re-measured on Bedrock or GPU infrastructure |
| Claude via Bedrock live | **BLOCKED** | AWS account (Phase 0) |
| Minimal web UI (plan week 3) | **Deferred** | Owner decision D5, moved to Phase 4 |
| Golden set on real approved documents | **BLOCKED** | Real data not permitted |

## I. Known issues and next steps

1. **Cross-language questions** (English question, Kannada document) are the main miss.
   Options: translate the query, or measure on Bedrock or Sarvam.
2. **Relevance floor tuned on the test set.** Build a separate validation set before any
   pilot.
3. **Latency:** about 18–29 s locally. Production needs Bedrock or GPU hosting, streaming
   responses and possibly smaller contexts.
4. **Not built yet (each to be justified by golden-set evidence):** reranker, answer cache,
   historical "as of date" answers, intent classifier.
5. **Logs:** retention policy, `audit_reader` access and a cost ceiling are still open.
6. **Verifier limit:** it proves a quote exists, not that the answer is correct. An
   "LLM-as-judge" faithfulness check could be added for real data.
7. **The branch isn't pushed.** `phase-2/prep` now also carries Phase 3; consider merging to
   `main` when you push.

## J. My learning notes

- **RAG = search first, then write from what you found.** The AI is only as good as the
  passages it is given; weak retrieval leads to guessing or "I don't know".
- **Never trust the AI's citations; check them.** We demand exact quotes and verify them.
  This turns "the AI says so" into "the document says so".
- **Abstaining is a success, not a failure.** A wrong answer about leave or pay does real
  harm. Our gate refuses before even calling the model when evidence is weak.
- **Permissions come before the AI, always.** If the model never sees restricted text, no
  clever prompt can make it leak.
- **Prompt injection can't be fixed by prompting alone.** It takes layers: fencing, no
  tools, permission filtering and measurement.
- **Measure with a fixed exam.** The golden set makes "is it good?" a number we can track,
  and the answer key is written by hand so the system can't grade itself.
- **Beware tuning on the exam.** The relevance floor was set on the golden set; a separate
  validation set is needed before believing it.
- **Rejected alternatives:**
  - a paid API locally: cost and data leaving the machine;
  - giving the model a search tool: a larger injection surface;
  - dense-only search: missed exact codes;
  - adding a reranker now: a 2.2 GB download without evidence it's needed.
- **Before Phase 4, understand** what a pilot needs beyond correctness: a UI, onboarding,
  support, monitoring, and the real-data gate.

## K. Go/no-go recommendation

**GO for Phase 4 locally with synthetic data. NO-GO for any real users or real documents.**

- Every mandatory security criterion passed with evidence: 0 permission leaks across all
  tests, no fabricated citations, abstention working, injection probes resisted.
- Quality targets were met on the synthetic golden set, with the caveats in F.
- The one failed criterion, p95 ≤ 6 s, is a performance target that depends on production
  hosting (Bedrock), not a security failure. It must be re-measured before a pilot.
- A real pilot (Phase 4's "20 real users") stays blocked by Phase 0: Group SSO, the AWS
  account and Bedrock, legal approval for real data, and the release gate.

Please confirm:
1. GO or NO-GO for Phase 4 (local, synthetic).
2. Whether to push the branch, and whether to merge it into `main`.

I will not start Phase 4 until you say so.
