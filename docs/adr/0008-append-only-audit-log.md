# ADR-0008: Append-only audit log, written before the response

- **Status:** Accepted by the owner on 2026-10-19. Builds on the approved permissions design §9.
- **Date:** 2026-10-18

## Context
The plan's Phase 1 done-criterion is "audit log captures every request". The permissions
design (§9, approved) requires an append-only `audit_event` table: the app role may only
INSERT and SELECT, a trigger blocks UPDATE and DELETE, and records carry no document text,
tokens or personal data. Several details were left open.

## Decision
1. **One event per `/api/` request**, written by `AuditMiddleware`: request ID, action
   (route template, e.g. `GET /api/v1/documents/{document_id}`), method, raw path without
   the query string, status, outcome (`success` / `denied` / `rejected` / `error`),
   organisation, actor, and target (e.g. `document` + ID). `/health` and `/docs` are not
   audited, so the log is not flooded every few seconds by probes.
2. **Fail closed.** The event is written *before* the response starts. If the write fails,
   the caller receives a generic 500 and the original body is discarded: no data leaves
   the API unrecorded.
3. **Append-only for everyone.** UPDATE, DELETE and TRUNCATE are revoked from the app role,
   *and* a trigger rejects them for every role, including the owner.
4. **No foreign keys.** An audit record must outlive the user, document, or organisation it
   names, so it stores plain IDs.
5. **Tenant isolation.** RLS: a tenant reads only its own events. Requests that never signed
   in produce events with no organisation; they may be inserted from an unscoped
   transaction but are readable only by operators (the owner account). Events are inserted
   without `RETURNING`, because RETURNING would apply the read policy to the new row.

## Alternatives considered
- **Write after the response is sent**: faster, but a failed write would leave a request
  that returned data with no trace.
- **Log-only audit (structured logs)**: logs can be dropped or edited, and can't be
  queried with tenant isolation. They remain a secondary signal.
- **Domain events only (e.g. `document.read`)**: richer, but would miss requests that fail
  before reaching a handler. Request-level events can be enriched with domain events later.
- **Trigger-free append-only (grants only)**: the owner or a compromised migration could
  still rewrite history.

## Why it fits Lekha
"A user saw something they shouldn't have" is the plan's hard blocker. A complete,
tamper-resistant record of every access is how such an incident would be investigated.

## Trade-offs
- Every API request costs one extra INSERT, and an audit-store outage takes the API down
  (deliberately).
- Org-less events (unauthenticated requests) are visible only to operators.
- The table grows forever until retention and WORM export exist (production items).
- Downgrading migration 0003 drops the history; it must never run in production.

## Security and operational implications
The trigger also blocks legitimate retention purges. A future retention job will need a
reviewed, audited procedure: export to WORM storage, then drop old partitions.

## Cost
Negligible locally; storage grows with traffic.

## Revisit when
Request volume makes synchronous writes a bottleneck (consider a durable queue with
at-least-once delivery), or when `GET /api/v1/audit` for `audit_reader` is built and needs
per-business-unit scoping (the plan says audit is "scoped to the caller's business unit").
