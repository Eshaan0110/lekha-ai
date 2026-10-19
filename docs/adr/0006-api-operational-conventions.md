# ADR-0006: Error envelope, request IDs, and health endpoints

- **Status:** Proposed. Error envelope aligned with the Implementation Plan on 2026-10-19 at the owner's request.
- **Date:** 2026-10-17

## Context
CLAUDE.md requires a common error envelope, request-ID correlation, and health and
readiness endpoints. The exact shapes are defined in the Implementation Plan, which is not
yet in the repository.

## Decision
- **Error envelope (the plan's exact shape):** `{"error": {"code", "message", "detail": {}}}`.
  Codes are stable snake_case strings from the plan's table (`unauthenticated`, `forbidden`,
  `not_found`, `conflict`, `validation_error`, `rate_limited`, `upstream_unavailable`) plus
  `internal_error`, `bad_request`, `payload_too_large`, `unsupported_media_type` and
  `length_required`. `detail` is always an object; validation errors put each field's
  location and reason in `detail.fields`, never the submitted value. The request ID is not
  in the body: the plan puts it in the `X-Request-ID` header of every response.
  Unhandled exceptions become a generic 500; the stack trace is logged on the server only.
- **Request ID:** `X-Request-ID` is accepted only if it matches `^[A-Za-z0-9._-]{1,64}$`;
  otherwise a UUID4 hex value is generated. It is returned on every response and attached
  to every log line. This is implemented as plain ASGI middleware so context variables
  are reliable.
- **Health:** unversioned `GET /health/live` (no dependency calls) and `GET /health/ready`
  (probes every dependency concurrently with a 2-second timeout each; 503 if any fails;
  coarse status only). Business APIs go under `/api/v1/`.

## Alternatives considered
- **RFC 9457 Problem Details**: a published standard and a reasonable alternative; we can
  adopt it if the Implementation Plan prefers it.
- **Health under `/api/v1/health`**: health is operational, not a versioned product API.

## Why it fits Lekha
Uniform errors simplify clients and prevent information leaks. Request IDs make auditing
and debugging traceable end to end.

## Trade-offs
A custom envelope is not a published standard.

## Security and operational implications
Readiness reveals which dependency is down, so in production it must be reachable from
the internal network only.

## Cost
None.

## Revisit when
The Implementation Plan specifies different conventions.
