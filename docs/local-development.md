# Local development guide

## Machine requirements

Measured on the reference laptop (2026-10-17): Windows 11, Ryzen 5 5600H (6 cores),
15.4 GB RAM, RTX 3050 4 GB, Docker Desktop with 8 GB allocated.

| Need | Phase 1 | Later phases |
|---|---|---|
| RAM for containers | ~2.3 GB limit total (Postgres 512 MB, Redis 256 MB, Qdrant 1 GB, object store 512 MB) | BGE-M3 needs ~2–3 GB more on CPU |
| Disk | ~1.2 GB images + data | BGE-M3 weights ~2.3 GB (download only after an explicit check) |
| GPU | Not needed | Optional for embeddings; never required to start the API or tests |

> Free RAM was under 1 GB when measured. Close heavy apps if containers are slow to start.

## Tools

- **Docker Desktop** (Compose v2).
- **uv** — installs Python 3.12 automatically from `.python-version` and creates `.venv`.
  You do not need to install Python yourself.

## Ports (all bound to 127.0.0.1)

Non-default ports avoid clashing with other local projects (5432 and 6333 were already in use).

| Service | Host port | Container port |
|---|---|---|
| PostgreSQL 16 | 15432 | 5432 |
| Redis | 16379 | 6379 |
| Qdrant HTTP | 16333 | 6333 |
| Object storage (S3 API) | 18333 | 8333 |
| Lekha API | 8000 | — |

## Command sequence

```powershell
./dev.ps1 setup             # 1. once: deps + .env with random secrets + object-store creds file
./dev.ps1 up                # 2. start stack and wait until every container is healthy
./dev.ps1 status            #    see health
./dev.ps1 migrate           # 3. apply migrations + create the API's limited DB login
./dev.ps1 seed              #    load the synthetic organisation and users (safe to re-run)
./dev.ps1 run               # 4. API with auto-reload
./dev.ps1 worker            #    background ingestion worker (second terminal)
./dev.ps1 check             # 5. before every commit
./dev.ps1 test-integration  # 6. with the stack running
./dev.ps1 down              # 7. stop (volumes kept)
```

Verify the API: open http://127.0.0.1:8000/health/ready — every check should be `"ok"`.

## Secrets

`./dev.ps1 setup` writes `.env` with a fresh 64-hex-character secret for every
`CHANGE_ME` in `.env.example`, and writes `infra/local/generated/s3.json` for object
storage. Both are git-ignored. The app refuses to start with a placeholder or short
secret. To rotate: `./dev.ps1 down`, delete `.env` **and** the Docker volumes
(`docker volume rm lekha_postgres-data ...` — this deletes local data), then `setup` and `up`.

## Backup and restore (all local data)

```powershell
./dev.ps1 backup                                  # -> backups/<UTC stamp>/
./dev.ps1 verify-backup -From backups/<stamp>     # check every file against the manifest
./dev.ps1 restore -From backups/<stamp> -Yes      # REPLACES current data
```

A backup holds `postgres.dump` (database, grants, audit log), one Qdrant snapshot per
collection, every stored file, and `manifest.json` with each file's SHA-256. Restore checks
every file first and changes nothing if anything is missing or altered. Backups contain
document data: they are git-ignored and must stay on this machine. Details: ADR-0010.

## Signing in locally (dev-only)

Real company sign-in (Group OIDC) is not available yet. Locally, `LEKHA_AUTH_MODE=dev` lets
you act as a **synthetic** user by sending a header; the app refuses this mode outside
`local`/`test`.

```bash
curl -H "X-Dev-User: dev|asha" http://127.0.0.1:8000/api/v1/documents
```

| User | Sees |
|---|---|
| `dev|asha` | Alpha leave policy |
| `dev|meera` | Alpha leave policy, Alpha legal memo, Alpha travel policy |
| `dev|ravi` | Beta leave policy |
| `dev|chitra` | nothing yet, but may **upload** to alpha (curator) |
| `dev|kiran`, `dev|nobody` | nothing |
| `dev|inactive` | cannot sign in (401) |

Why each user sees what they see is explained in `apps/scripts/seed_dev_data.py`.
Rebuild the synthetic organisation with `./dev.ps1 seed -Reset` (it only ever deletes the
organisation named "Synthetic Dev Org …").

Uploading (as the curator; `metadata` is JSON, `acl_subjects` lists who may read it):

```bash
curl -H "X-Dev-User: dev|chitra" -F "file=@handbook.pdf" -F 'metadata={"title":"Handbook","canonical_key":"synthetic:handbook","business_unit_id":"<alpha id from /api/v1/me>","classification":"internal","acl_subjects":["user:<your user id>"]}' http://127.0.0.1:8000/api/v1/documents
```

## Database accounts

| Account | Used by | Can do |
|---|---|---|
| `LEKHA_POSTGRES_USER` (owner) | migrations, provisioning, `psql` for debugging | everything; bypasses row-level security |
| `LEKHA_DB_APP_USER` (`lekha_app`) | the API | read/write rows only, and only for the organisation set in the current transaction |

If you `psql` as the owner you will see every tenant's rows. That is expected; the API never uses that account.

## Tests

- `./dev.ps1 test` — unit tests; no Docker; never read your `.env`.
- `./dev.ps1 test-integration` — marked `integration`; uses `.env` and the running stack. It recreates a separate **`lekha_test`** database on every run, so your `lekha` dev data is never touched.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `.env not found` | Run `./dev.ps1 setup`. |
| Settings error mentioning `CHANGE_ME` | `.env` was hand-copied; delete it and run `setup`. |
| `up` hangs on first run | Images are downloading (~1 GB); wait, or check `docker compose ... logs`. |
| Port refused or not published after a restart | Windows reserves dynamic port blocks (49152+); check `netsh int ipv4 show excludedportrange protocol=tcp` and keep ports below 49152. |
| Port already allocated | Another project uses the port; change `LEKHA_*_PORT` in `.env` (and `LEKHA_QDRANT_URL` / `LEKHA_S3_ENDPOINT_URL`). |
