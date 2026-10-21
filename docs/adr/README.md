# Architecture Decision Records

An ADR records one significant decision: the context, what we chose, what we rejected,
and when we would revisit it. Statuses: **Proposed** (awaiting owner approval),
**Accepted** (approved, or part of the approved CLAUDE.md baseline), **Superseded**.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith with clean layering | Accepted (CLAUDE.md baseline) |
| [0002](0002-python-toolchain.md) | Python 3.12 toolchain: uv, lockfile, quality gates | Proposed |
| [0003](0003-local-infrastructure.md) | Local infrastructure with Docker Compose | Proposed |
| [0004](0004-postgres-driver-asyncpg.md) | asyncpg as the single PostgreSQL driver | Proposed |
| [0005](0005-local-object-storage.md) | SeaweedFS as local S3-compatible storage (MinIO unavailable) | Accepted (2026-10-18) |
| [0006](0006-api-operational-conventions.md) | Error envelope, request IDs, health endpoints | Proposed |
| [0007](0007-database-least-privilege-and-tenant-isolation.md) | Least-privilege DB account and tenant isolation (RLS) | Accepted (2026-10-18) |
| [0008](0008-append-only-audit-log.md) | Append-only audit log, written before the response | Accepted (2026-10-19) |
| [0009](0009-document-upload.md) | Document upload: validation, authorisation and storage | Accepted (2026-10-19) |
| [0010](0010-local-backup-and-restore.md) | Local backup and restore of all three data stores | Accepted (2026-10-19) |
| [0011](0011-idempotency-keys.md) | Idempotency-Key for write endpoints | Accepted (2026-10-19) |
| [0012](0012-embeddings-runtime.md) | Embedding models and how they run locally (BGE-M3 via ONNX Runtime) | Accepted (2026-10-21) |

Template: copy any ADR and keep every heading.
