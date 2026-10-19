# ADR-0005: SeaweedFS as local S3-compatible object storage

- **Status:** Accepted by the project owner on 2026-10-18 (local development only; production object storage remains AWS S3).
- **Date:** 2026-10-17

## Context
The approved stack lists **MinIO** for local object storage. On 2026-10-17 the MinIO
community images could not be pulled: `minio/minio` on Docker Hub returned "object not
found", and `quay.io/minio/minio:latest` and a 2025 release tag were also missing. MinIO's
community edition is now distributed as source only.

Production object storage is expected to be **AWS S3** (Mumbai), so the application must
speak the **S3 API**. The local server is only a stand-in for it.

## Decision
Use **SeaweedFS 4.48** (`chrislusf/seaweedfs`, Apache-2.0) in single-node `server -s3`
mode, for local development only. Credentials come from
`infra/local/generated/s3.json`, which is generated from `.env`. The application will talk
to it only through an S3 client, never through SeaweedFS-specific APIs, so switching to
MinIO, S3, or any other S3-compatible store needs only configuration.

## Alternatives considered
- **Build MinIO from source**: keeps the name, but we would maintain our own image of an
  AGPL project that has no upstream community binaries.
- **Garage**: light and S3-compatible, but needs a manual cluster-layout bootstrap; AGPL.
- **RustFS**: Apache-2.0, but young.
- **Local filesystem adapter**: simplest, but it would not exercise S3 behaviour.

## Why it fits Lekha
Apache-2.0, actively maintained, a single container, and a real S3 API with authentication.

## Trade-offs
S3 compatibility is broad but not complete; integration tests must cover the S3
operations we use (put, get, head, delete, multipart). Readiness currently probes
`/healthz` only, not credentials; a credential check arrives with the storage adapter.

## Operational note
SeaweedFS creates 7 volumes per bucket. The original `-volume.max=8` left no room for a
second bucket (the integration-test bucket failed with "No writable volumes"); it is now 64.

## Security and operational implications
Anonymous access returns 403 (verified). The port is bound to localhost.

## Cost
Free.

## Revisit when
The owner prefers another option, or MinIO publishes maintained images again.
