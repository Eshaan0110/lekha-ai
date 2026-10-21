# Phase tracker

Last updated: 2026-10-17. Status vocabulary (CLAUDE.md §11):
**Proposed · Implemented · Tested · Approved · Blocked**. "Approved" is set only by the
project owner, never by the implementer.

## Discrepancies and open questions

| # | Item | Impact | Status |
|---|---|---|---|
| D1 | **Implementation Plan found** in the owner's Downloads folder and copied to [`docs/implementation-plan/`](implementation-plan/) (primary plan of 16 Oct 2026, plus the detailed *Technical Architecture and Roadmap*). Permissions design drafted from them: `docs/design/permissions-v1.md`. | ACL model now defined; small differences listed in the design doc §12. | Design proposed — awaiting owner review |
| D2 | **MinIO community container images are no longer published** (Docker Hub `minio/minio` and `quay.io/minio/minio` tags were not pullable on 2026-10-17). SeaweedFS is used as the local S3-compatible stand-in. | Local-only; production object storage is expected to be AWS S3. | Approved 2026-10-18 — ADR-0005 |
| D3 | Python driver: asyncpg instead of a psycopg-only setup. | None on the approved stack (PostgreSQL + SQLAlchemy unchanged). | Proposed — ADR-0004 |

## Phase 0 — Discovery and external dependencies (tracked, not implemented here)

| Dependency | Owner | Status |
|---|---|---|
| Pilot business unit selected | Business | Open |
| Clavis scope agreed | Business | Open |
| Group identity provider (OIDC) readiness; test tenant + client registration | Group IT | Open |
| AWS account (Mumbai) provisioned with guardrails | Group IT / Cloud | Open |
| Bedrock (Claude, Mumbai) access and processor contract | Legal / Procurement | Open |
| Sarvam deployment model and data-residency validation | Legal / Security | Open |
| DPDP legal review and data-processing agreements | Legal | Open |
| Approval to ingest any real (non-synthetic) documents | Security / Legal | Open — not permitted |

## Phase 1 — Secure foundation (local)

Overall: **Build work complete; end-of-phase report written ([reports/phase-1-report.md](reports/phase-1-report.md)); awaiting owner go/no-go.** Slices 1 (bootstrap and infrastructure), 2 (database roles; identity/document/ACL schema) 3 (access rule, AuthContext, dev sign-in, protected `/api/v1`), 4 (append-only audit log), 5 (document upload), 6 (full local backup and restore drill) and 7 (Idempotency-Key) are complete.

| Criterion (CLAUDE.md §8) | Status | Evidence / notes |
|---|---|---|
| Repository and application skeleton | Tested | `apps/`, `lekha/core/`, `db/`, `tests/`; import contracts kept |
| Docker Compose: PostgreSQL, Qdrant, MinIO, Redis | Tested (object storage = SeaweedFS, approved, see D2) | `infra/local/docker-compose.yml`; integration readiness test |
| FastAPI application | Tested | `apps/api/main.py`; unit + integration tests |
| Validated configuration and local secret handling | Tested | `lekha/core/config.py`; `dev.ps1 setup` generates secrets; `test_config.py` |
| SQLAlchemy models and Alembic migrations | Tested | `db/schema/`, migrations 0001–0002; round trip + `alembic check` on a throwaway `lekha_test` database |
| User, document, version, ACL schema | Tested | 11 tables per permissions-v1; RLS (with a guard test that fails if any tenant table lacks it), composite tenant FKs, badge checks; `test_schema_security.py` (15 tests) |
| Protected boundaries + dev-only auth adapter | Tested | `apps/api/auth.py`, `lekha/auth/`; router-level auth on `/api/v1`; dev adapter refused outside local/test; `test_dev_auth.py`, `test_access_rule.py`, `test_api_isolation.py` (16 tests incl. SQL-vs-rule agreement) |
| File upload infrastructure and validation | Tested | `POST /api/v1/documents`: content-based PDF/DOCX detection, macro refusal, size limit before and after reading, curator + clearance rule, confidential release gate, SHA-256 verified by storage, orphan clean-up; ADR-0009 (Accepted); `Idempotency-Key` with replay, per-user keys and clash detection (ADR-0011, Accepted); `test_upload_validation.py`, `test_uploads.py`, `test_idempotency.py` |
| Audit log infrastructure | Tested | `audit_event` (migration 0003), `AuditMiddleware`: one event per `/api/` request, written before the response (fail closed), append-only for every role incl. owner, tenant RLS; `test_audit.py`, `test_audit_log.py`. Reader endpoint `GET /api/v1/audit` not yet built |
| Request-ID correlation | Tested | `apps/api/middleware.py`; `test_api.py` |
| Structured logging | Tested | `lekha/core/log.py`; JSON log test |
| Health and readiness endpoints | Tested | `/health/live`, `/health/ready`; unit + integration |
| Basic automated tests | Tested | 137 unit + 76 integration tests passing |
| Backup and restore (local) | Tested | PostgreSQL + Qdrant + object storage with a checksummed manifest; verify-before-restore; automated restore drill (`test_full_restore_drill`) and damaged-backup refusal; ADR-0010 (Accepted). Production backups (RDS PITR, S3 versioning) pending infrastructure |
| Group OIDC integration | Blocked | Phase 0: IdP access |
| AWS deployment | Blocked | Phase 0: AWS account; not authorised |

### Next slices (proposed order)

1. ~~Least-privilege database roles~~ — done (slice 2, ADR-0007).
2. ~~Identity and ACL schema~~ — done (slice 2).
3. ~~Access rule, AuthContext, dev-only sign-in, protected `/api/v1`, isolation tests~~ — done (slice 3).
4. ~~Audit-log table and writer (append-only), correlated by request ID~~ — done (slice 4, ADR-0008).
5. ~~Object-storage adapter (S3 API) + upload endpoint with size/type validation~~ — done (slice 5, ADR-0009).
   ~~`Idempotency-Key` on write endpoints~~ — done (slice 7, ADR-0011). Still open from the plan: audit reader endpoint (`GET /api/v1/audit`).
6. ~~Backups for Qdrant snapshots and object storage; restore drill~~ — done (slice 6, ADR-0010).

## Phase 2 — Document ingestion (local, synthetic data only)

Overall: **Build work complete; end-of-phase report written ([reports/phase-2-report.md](reports/phase-2-report.md)); owner **GO for Phase 3** (2026-10-21, local, synthetic only).** ADR-0012 accepted 2026-10-21.

Plan approved with the recommended decisions D1–D4 ([design/phase-2-plan.md](design/phase-2-plan.md)).
**Prototype pipeline built and tested** (2026-10-20):

| Item | Status | Evidence |
|---|---|---|
| Background jobs (ARQ worker, `ingestion_job`, per-stage status, retries) | Tested | `apps/worker/`, `GET /api/v1/ingestion-jobs/{id}`; live run with the real worker on the dev stack |
| Extract (pypdf, python-docx), language by script, heading-aware chunks | Tested | `lekha/ingestion/`, `test_ingestion.py` |
| Embeddings | Tested: **fake** (tests) and **all-MiniLM-L6-v2** (real, English only, interim); **BGE-M3** working (ONNX Runtime, CPU, checksum-verified): English question ↔ Kannada answer similarity 0.74; ~0.06 s per text | `model_layer/minilm.py`: numpy forward pass, weights checksum-pinned, output matches sentence-transformers 6.1.0 to 1.2e-7; live demo found the right policy for each plain-English question. BGE-M3 (multilingual) still waits for the ~5 GB download |
| Qdrant index with permissions in every point | Tested | payload: tenant, BU, classification, `allowed_subjects`, current version |
| Searches without permission filters rejected | Tested | `UnscopedSearchError`; `test_a_search_without_permission_filters_is_refused` |
| Permission-filtered search (`POST /api/v1/search`) + database re-check | Tested | `test_upload_is_ingested_and_found_only_by_permitted_users`, `test_stale_index_is_caught_by_the_database_recheck` |
| Document versioning | Tested | Same `canonical_key` adds a version; it becomes current only after indexing (publish stage); a late older version never replaces a newer one; identical re-uploads and unit/classification changes refused (409); old points kept but flagged not-current in Qdrant |
| Deletion workflow (`POST /api/v1/deletion-requests`) | Tested | Curators only; document hidden at once (soft delete + `deletion_request`, migration 0006); worker purges vectors, chunks and files and records counts; audited; retries on outage; a document deleted mid-ingestion is never indexed. Retention window not applied yet (no retention policy from Phase 0) |
| 100 synthetic documents, 50-question red-team | Tested | `test_100_documents_and_a_50_question_red_team_with_zero_anomalies`: 100 synthetic docs (English PDF/DOCX + Kannada DOCX, 11 sharing patterns incl. confidential, expired shares, deleted, mis-tagged, prompt-injection text) all ingested; 50 queries × 7 users = 350 searches, **0 anomalies** against a hand-written answer key; every reader finds every document they may read. Run with the fake embedder **and with real BGE-M3**: 0 anomalies in both. Known gap: BGE-M3 finds 87% of documents by exact reference code (meaning search is weak at near-identical documents told apart only by a code); keyword/hybrid search is planned for Phase 3 |

Tests: 173 unit + 91 integration passing (with BGE-M3 present; its tests skip when the model is absent).

Owner decisions (2026-10-20): **Hindi is deferred** (script detection stays; Hindi leaves the
corpus and acceptance for now). Use **all-MiniLM-L6-v2** as the real embedder until BGE-M3 can
be downloaded; it understands English only, so Kannada search waits for BGE-M3.

## Phase 3 — Grounded Q&A (local, synthetic data only)

**Build work complete; end-of-phase report written ([reports/phase-3-report.md](reports/phase-3-report.md)); awaiting owner go/no-go for Phase 4.** Owner GO 2026-10-21. Plan approved with the recommended D1–D6: [design/phase-3-plan.md](design/phase-3-plan.md). Building.

| Step | Status | Evidence |
|---|---|---|
| 1. Model layer (LLM interface, Ollama, Bedrock) | Tested | `test_llm_adapters.py` (Ollama via fake server; Bedrock via botocore Stubber, never live); config guards in `test_config.py` |
| 2. Generation core (fenced prompt, citation verifier, abstention) | Tested | `test_generation.py`; import contract keeps `lekha.generation` pure |
| 3. Hybrid retrieval (Postgres full-text + RRF) | Tested | Migration 0007; `test_keyword_search_finds_exact_codes_and_obeys_permissions`; red team still 0 anomalies; BGE-M3 exact-code recall 87% → **100%** with hybrid |
| 4. `/api/v1/ask`, logs, rate limit | Tested | Migration 0008 (query_log, answer_log, citation, RLS); `test_ask.py` (13): cited answers name their version; the model is never shown unreadable text (prompts captured); abstains without calling the model on no/weak evidence; invented or unparseable replies never returned; 503 on model outage or none configured; per-user Redis rate limit fails closed (429/503); deletion wipes logged answers that quoted the document |
| 5. Golden set and evaluation | Tested | 42 synthetic policies, 100 hand-keyed questions; `test_golden.py` (fake model: 0 permission violations on every run); real run BGE-M3 + qwen3.5:9b: [eval/golden-ollama-bge-m3.md](eval/golden-ollama-bge-m3.md) — all quality targets met, 0 violations, 0 injection failures, p95 28.8 s (target 6 s, laptop) |

## Phase 4 — Daily-use companion (local, synthetic data only)

Owner said go ahead (2026-10-21). Plan approved with the recommended D1–D6: [design/phase-4-plan.md](design/phase-4-plan.md). Building.

| Step | Status | Evidence |
|---|---|---|
| 1. Demo web app (React + TypeScript + Vite) | Tested | `frontend/`: persona sign-in, Ask with live progress (`POST /api/v1/ask/stream`, progress only, answer sent after verification), clickable citations → source passage (`GET /api/v1/chunks/{id}`, same access rule), feedback (`POST /api/v1/answers/{id}/feedback`, migration 0009), documents list and curator upload; `logic.test.ts` (6), `test_ask.py` (+5); live run in the browser with BGE-M3 + qwen3.5:9b: cited answers in 30–41 s warm, permission refusals in < 1 s; [demo-script.md](demo-script.md) |
| 2. Meeting import | Tested | ADR-0015 (Accepted 2026-10-21). `POST/GET /api/v1/meetings`, `GET /api/v1/meetings/{id}`; .vtt/.srt/.txt parsing (`test_transcript.py`, 14); migration 0010; participant-only access (curators who weren't present see nothing); re-import = new version (source ID, or organiser ±2 min); citations give the exact turn time; organiser may delete; `test_meetings.py` (10); live: 4 demo meetings, cited answer from a meeting with the local model, private pay meeting withheld from Asha |
| 3. Minutes with approval | Tested | Migration 0011; `POST /api/v1/meetings/{id}/minutes` (any participant drafts), `POST /api/v1/minutes/{id}/approve|reject` (organiser only, optional edits); every decision and action needs a verbatim transcript quote or is dropped; approved minutes are indexed with the meeting (participant-only) and go stale when a new transcript is imported; deleted with the meeting; `test_minutes.py` (6), `test_meetings.py` (+5). Live with qwen3.5:9b: 1 decision + 3 actions, all quoted, owners left blank rather than invented; **drafting took 112 s** (plan target < 60 s for a 30-minute meeting: not met on this laptop) |
| 4. Glossary | Not started | |
| 5. Pilot support | Not started | | The real 20-user pilot stays blocked on Phase 0.

## Phases 5–7


Not started. Must not begin before Phase 1 acceptance and explicit owner approval.
