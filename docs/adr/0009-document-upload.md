# ADR-0009: Document upload: validation, authorisation and storage

- **Status:** Accepted by the owner on 2026-10-19
- **Date:** 2026-10-19

## Context
Phase 1 requires "file upload infrastructure and validation" (plan, day 4: "File upload
endpoint; MinIO storage; checksums; type + size validation"). The plan defines
`POST /api/v1/documents` (multipart file + metadata JSON) returning `document_id`,
`version_id` and `ingestion_job_id`, and forbids confidential data until the Phase 2
release gate passes.

## Decision
1. **Type from contents, not names.** Only PDF and DOCX are accepted (the plan's ingestion
   scope). The type is decided by magic bytes and structure, and the file extension must
   agree. Word files with macros (`vbaProject.bin`) are refused. DOCX archives are bounded
   (entries, uncompressed size) to block ZIP bombs before Phase 2 extraction.
2. **Size checked twice:** from `Content-Length` *before* the body is read (413 without
   storing anything), and again on the actual bytes. Default limit 25 MiB.
3. **Who may upload:** an active `curator` role in the target business unit, classifying
   no higher than their own clearance there. **Release gate:** `confidential` and
   `restricted` uploads are refused while `LEKHA_ALLOW_CONFIDENTIAL_UPLOADS` is false (the
   default), and settings refuse to switch it on in `local`/`test`.
4. **Guest list required:** at least one existing badge. Unknown badges → 422. No implicit
   access, not even for the uploader.
5. **Storage keys contain only IDs:**
   `{organisation}/{business_unit}/{document}/v{n}/{file}.{pdf|docx}`. The sanitised
   original filename is stored as data only.
6. **Integrity:** SHA-256 is computed by Lekha, stored in the database, and sent to S3 as
   `ChecksumSHA256`, so storage refuses a body corrupted in transit (verified: `BadDigest`).
7. **Ordering:** checks → store bytes → write rows in one tenant transaction. If the
   database write fails (e.g. a concurrent upload claimed the same `canonical_key`), the
   stored object is deleted. Storage failure → `503 upstream_unavailable` with no rows.
8. `ingestion_job_id` is always `null` until ingestion jobs exist (Phase 2).
9. Readiness now checks object storage with an authenticated `HEAD` on the bucket.

## Alternatives considered
- **Trust Content-Type / extension**: trivially spoofed; rejected.
- **Write rows first, then store**: a crash would leave rows pointing at a missing file,
  which is worse than an orphaned object, and orphans are cleaned up anyway.
- **Pre-signed direct-to-S3 uploads**: avoids passing bytes through the API, but needs
  post-upload validation and is more complex; worth revisiting for large files.
- **Antivirus scanning now**: valuable, but needs a scanner service (e.g. ClamAV); deferred
  to the ingestion pipeline.

## Why it fits Lekha
Uploads are where untrusted bytes first enter the system. Strict typing, a closed release
gate and explicit guest lists mean nothing reaches storage that the plan does not allow.

## Trade-offs
- The whole file is held in memory (≤ 25 MiB) during validation.
- **No `Idempotency-Key` support yet**, although the plan asks for it on write endpoints.
  `canonical_key` uniqueness prevents duplicate documents, but a retried request returns
  409 instead of the original response. Tracked as open work.
- No malware scanning; macros are refused, but other active content is not inspected.

## Security and operational implications
Production storage must use SSE-KMS, bucket versioning and an IAM policy restricted to the
organisation/BU prefixes. The orphan-cleanup path is best-effort: a failed delete is logged
for an operator.

## Cost
Negligible locally; production S3 storage and requests are in the plan's budget.

## Revisit when
Files above ~25 MiB are needed (switch to pre-signed uploads), malware scanning is required
by the security review, or the idempotency work lands.
