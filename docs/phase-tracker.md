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

Owner GO on 2026-10-19 for local development with synthetic documents only. Before building:
the error envelope now matches the plan exactly (done), and the Phase 2 plan is
**Proposed**: [design/phase-2-plan.md](design/phase-2-plan.md), with four owner decisions
(D1 extractor, D2 language detection, D3 BGE-M3 download and runtime, D4 synthetic corpus).

## Phases 3–7


Not started. Must not begin before Phase 1 acceptance and explicit owner approval.
