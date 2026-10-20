# Architecture overview

Lekha is a **modular monolith** (ADR-0001): one deployable Python application whose
business modules have strict boundaries, so a module could later be extracted into a
separate service without rewriting it.

## Layers

| Layer | Responsibility | Where |
|---|---|---|
| Interfaces | HTTP routes, request/response schemas, middleware, entry points | `apps/` |
| Application | Use cases and orchestration | `lekha/<module>/` (from the schema slice onward) |
| Domain | Business entities, rules, invariants — no framework imports | `lekha/<module>/` |
| Infrastructure | Database, storage, queues, model providers | `db/`, adapters inside modules |
| Platform core | Configuration, logging, health-check runner | `lekha/core/` |

## Current repository layout

```
apps/api/            FastAPI entry point: app factory, middleware, error envelope, health routes
apps/scripts/        Operational scripts (provision the API's least-privilege DB login)
lekha/core/          Validated settings, structured logging, readiness-check runner
lekha/auth/          Access-control vocabulary (classification, badges); pure Python
db/                  Engine (app vs owner account), tenant transactions, Alembic migrations
db/schema/           ORM mappings: identity (org, BU, user, role, grant, badge), documents (doc, version, file, ACL)
db/access.py         Load a caller's AuthContext; the access rule as SQL (readable documents only)
apps/api/auth.py     The sign-in dependency every /api/v1 route uses (fails closed with 401)
apps/api/audit.py    Audit middleware: one append-only event per /api/ request, before the response
lekha/audit/         Audit event vocabulary (outcomes, event shape, sink interface); pure Python
db/audit.py          PostgreSQL audit sink
db/documents.py      Writes for new uploads (document, file, version, guest list)
lekha/memory/        Upload validation (pure), object-store interface, S3 adapter
apps/api/uploads.py  POST /api/v1/documents
apps/api/search.py   POST /api/v1/search, GET /api/v1/ingestion-jobs/{id}
apps/worker/         ARQ worker and the ingestion pipeline (extract → chunk → embed → index)
lekha/ingestion/     Text extraction, language by script, heading-aware chunking (pure)
lekha/retrieval/     Permission filter from AuthContext; Qdrant adapter with the unscoped-search guard
model_layer/         Embedder interface; fake embedder (simulation); all-MiniLM-L6-v2 (real, English, numpy); BGE-M3 adapter (gated)
apps/api/idempotency.py, db/idempotency.py  Idempotency-Key: claim, replay, complete, release
apps/api/routes_v1.py  /api/v1/me, /api/v1/documents (cursor paging), /api/v1/documents/{id}
tests/unit/          Fast tests, no Docker required
tests/integration/   Tests against the real local Docker stack
infra/local/         Docker Compose for the local stack
dev.ps1              Repeatable developer commands
```

Not yet created (by design — created when their phase starts): `apps/worker`,
`lekha/{memory,ingestion,retrieval,generation,speech,actions,connectors,mcp,audit}`,
`model_layer/`, `frontend/`.

### Deviations from the planned structure (CLAUDE.md §4.3)

| Deviation | Reason |
|---|---|
| `lekha/core/` added | Configuration, logging, and health checks are cross-cutting and belong to no business module. |
| `dev.ps1` at the repository root | Windows has no `make`; a root script is the most discoverable entry point. |
| Compose file in `infra/local/` | Keeps local-only infrastructure separate from future production infrastructure in `infra/`. |

## Dependency rules (enforced by `lint-imports`)

```mermaid
flowchart LR
    apps["apps/ (entry points)"] --> lekha["lekha/ (business modules + core)"]
    apps --> db["db/ (persistence)"]
    db --> core["lekha.core"]
    lekha -. "forbidden" .-> apps
    core -. "forbidden" .-> db
```

- `lekha` and `db` must never import `apps` (entry points depend on business code, never the reverse).
- `lekha.core` must not import `db` (configuration and logging stay free of ORM concerns).
- `lekha.auth` must not import `db`, `apps`, SQLAlchemy, FastAPI, or any network client: permission rules stay pure and unit-testable.
- `model_layer` must not import `lekha` (contract added when `model_layer` is created).

## Request flow (today)

```mermaid
sequenceDiagram
    participant C as Client
    participant M as RequestContextMiddleware
    participant R as Router / handler
    participant H as Readiness runner
    participant D as Postgres / Redis / Qdrant / S3

    C->>M: GET /health/ready (optional X-Request-ID)
    M->>M: accept safe ID or generate one; bind to log context
    M->>R: forward request
    R->>H: run checks concurrently, each with a timeout
    H->>D: SELECT 1 / PING / authenticated GET / health GET
    D-->>H: ok or failure (details logged, never returned)
    H-->>R: per-check status
    R-->>M: 200 ready or 503 not_ready
    M-->>C: response + X-Request-ID header; one structured access-log line
```

Any unhandled exception is caught by the middleware, logged with its stack trace and
request ID, and turned into a generic `500 internal_error` envelope.

## Trust boundaries

1. **Client → API.** All input is untrusted. Today the only inputs are the path, headers,
   and the `X-Request-ID` header (validated against a strict pattern).
2. **API → infrastructure.** Every service requires credentials, binds to `127.0.0.1`,
   and is called with timeouts.
3. **Documents → model** (from Phase 2/3). Document text is data, never instructions.

## Endpoints

| Path | Purpose | Auth |
|---|---|---|
| `GET /health/live` | Process is running; no dependency calls | None (operational) |
| `GET /health/ready` | All dependencies reachable; 503 otherwise | None locally; must be internal-only in production |
| `GET /docs`, `/openapi.json` | API documentation (local/test environments only) | None |

| `GET /api/v1/me` | The caller's identity, roles and clearance per business unit | Signed in |
| `GET /api/v1/documents?limit=&cursor=` | Documents the caller may read (cursor-paged) | Signed in |
| `GET /api/v1/documents/{id}` | One document; `404` if missing **or** not visible | Signed in |
| `POST /api/v1/search` | Permission-filtered search by meaning (`query`, `limit`) | Signed in |
| `GET /api/v1/ingestion-jobs/{id}` | Processing status of an upload | Curator of the unit, or a reader of the document |
| `POST /api/v1/documents` | Upload a PDF/DOCX (multipart `file` + `metadata` JSON); optional `Idempotency-Key` header for safe retries | Curator in the business unit |
