# ADR-0011: Idempotency-Key for write endpoints

- **Status:** Accepted by the owner on 2026-10-19
- **Date:** 2026-10-19

## Context
The plan's API conventions say write endpoints accept an `Idempotency-Key` header, and its
error table lists `409 conflict` for an "idempotency clash". Without it, a client whose
upload response is lost on a bad network cannot safely retry: the retry either duplicates
work or fails with `409` (canonical key already used), even though the first attempt
succeeded. This matters for WhatsApp and phone channels later.

## Decision
1. **Optional header** on `POST /api/v1/documents` (the only write endpoint so far). Keys
   are 8–255 characters of `[A-Za-z0-9._:-]`; a UUID is recommended. Malformed → `400`.
2. **Scoped per user** in table `idempotency_record`, unique on
   `(organisation_id, user_id, key)` with tenant row-level security. One user's key can
   never replay or block another user's request.
3. **Request fingerprint** = SHA-256 over the operation, the parsed metadata (canonical
   JSON), the file's SHA-256 and the sanitised filename. It is computed from the *meaning*
   of the request, not raw multipart bytes, so the random multipart boundary does not matter.
4. **Claim → work → complete or release**, each claim step in its own committed transaction:
   - first use → claim as `in_progress`, do the work, then store status, body and `Location`;
   - same key + same fingerprint, completed → replay the stored response with
     `Idempotent-Replayed: true`;
   - same key + different fingerprint → `409`; same key while in progress → `409`;
   - the work failed → the claim is deleted, so the client may correct and retry;
   - an in-progress claim with no progress for 5 minutes (e.g. the server crashed) may be
     taken over by the same request; keys expire after 24 hours.
   `INSERT … ON CONFLICT DO NOTHING` plus `SELECT … FOR UPDATE` lets exactly one of several
   concurrent requests win (tested with five at once).
5. Claiming happens **after** validation and the permission check, so invalid or forbidden
   requests never occupy a key.

## Alternatives considered
- **Fingerprint of raw request bytes**: differs on every retry because of the multipart
  boundary, which would make legitimate retries look like clashes.
- **Store failed responses too** (Stripe-style): unnecessary here, because a failed upload
  has no side effects and repeating it gives the same answer.
- **Rely on `canonical_key` uniqueness only**: prevents duplicates but cannot return the
  original success to a retrying client.

## Why it fits Lekha
It follows the plan's convention exactly, keeps retries safe on unreliable networks, and
reuses the same tenant-isolation and least-privilege rules as every other table.

## Trade-offs
- Two or three extra short transactions per keyed request.
- If the server crashes after the upload commits but before the key is completed, a retry
  within 5 minutes gets `409 in progress`, and a later one hits the `canonical_key` conflict:
  safe (no duplicate), but not a replay.
- Expired rows are removed only when their key is reused; a periodic purge job belongs with
  the background worker (Phase 2).

## Security and operational implications
Stored responses contain only IDs, the sanitised filename and checksums, never file
contents. Rows sit behind tenant RLS and are deleted with the organisation.

## Cost
Negligible.

## Revisit when
More write endpoints are added (apply the same helper), or retry volume makes the purge job
necessary.
