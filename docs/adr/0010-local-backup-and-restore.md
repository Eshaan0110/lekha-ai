# ADR-0010: Local backup and restore of all three data stores

- **Status:** Accepted by the owner on 2026-10-19
- **Date:** 2026-10-19

## Context
Phase 1 requires "backup and restore procedures for the local environment"; the plan's
Phase 1 done-criterion is "backup + restore tested". Slice 1 only dumped PostgreSQL.
Lekha now keeps data in three places: PostgreSQL (records, permissions, audit log), Qdrant
(vectors, from Phase 2) and object storage (the uploaded files).

## Decision
`apps/scripts/backup.py` (wrapped by `./dev.ps1 backup | verify-backup | restore`):

1. **One folder per backup**, `backups/<UTC stamp>/`: `postgres.dump` (pg_dump custom
   format, including grants and the audit log), one Qdrant snapshot per collection, and
   every object under its own key, plus `manifest.json` listing every file's SHA-256 and size.
2. **Never half a backup:** written to `<stamp>.partial` and renamed only when complete.
3. **Verify before restore:** restore first checks every file against the manifest, rejects
   unexpected files and paths that escape the folder, and changes nothing if any check
   fails. Restore also requires an explicit `--yes` / `-Yes`.
4. **Order:** PostgreSQL first when backing up (objects added afterwards are harmless
   orphans), and PostgreSQL → Qdrant → objects when restoring.
5. **Conservative restore:** collections or objects present now but absent from the backup
   are reported as warnings, not deleted.

## Alternatives considered
- **Docker volume tarballs**: simple, but they need the containers stopped, can't be
  verified per file, and are tied to Docker's on-disk formats.
- **PostgreSQL only**: leaves files and vectors unprotected.
- **Deleting extras on restore** (an exact mirror): more faithful, but one wrong folder
  would destroy data. Reporting is safer for a developer tool.

## Why it fits Lekha
The plan treats backup and restore evidence as a release-gate item. A checksummed,
verifiable backup with an automated restore drill (`test_full_restore_drill`) provides that
evidence locally.

## Trade-offs
- Not a single point in time across all three stores; acceptable locally, not in production.
- Objects are held in memory one at a time (fine at the 25 MiB upload limit).
- Backups contain document data and must be protected like the data itself; `backups/` is
  git-ignored.

## Security and operational implications
**Production is different.** It needs RDS automated snapshots with point-in-time recovery,
Qdrant snapshots shipped to S3, S3 versioning with cross-region copies inside India, WORM
export of the audit log, and a periodic restore drill with recorded RTO/RPO. All of this
is part of the production infrastructure and its release gate, not this script.

## Cost
None locally.

## Revisit when
Production infrastructure is provisioned, or local data volumes make full copies slow.
