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
| `dev|bina` | nothing yet, but may **upload** to beta (curator) |
| `dev|tara` | no documents; reads Alpha's **audit log** (audit_reader) |
| `dev|hari` | no documents; **approves** Alpha's ticket proposals (approver) |
| `dev|uma` | no documents; **approves** Beta's ticket proposals (approver) |
| `dev|kiran`, `dev|nobody` | nothing |
| `dev|inactive` | cannot sign in (401) |

Why each user sees what they see is explained in `apps/scripts/seed_dev_data.py`.
Rebuild the synthetic organisation with `./dev.ps1 seed -Reset` (it only ever deletes the
organisation named "Synthetic Dev Org …").

Uploading (as the curator; `metadata` is JSON, `acl_subjects` lists who may read it):

```bash
curl -H "X-Dev-User: dev|chitra" -F "file=@handbook.pdf" -F 'metadata={"title":"Handbook","canonical_key":"synthetic:handbook","business_unit_id":"<alpha id from /api/v1/me>","classification":"internal","acl_subjects":["user:<your user id>"]}' http://127.0.0.1:8000/api/v1/documents
```

## Real meaning search (interim English model)

By default the worker uses the **fake** embedder (a simulation). For real meaning search in
English, put the all-MiniLM-L6-v2 files (`model.safetensors`, `tokenizer.json`, ~90 MB, Apache-2.0)
in `models/all-MiniLM-L6-v2/` (git-ignored) and set in `.env`:

```
LEKHA_EMBEDDER=minilm
LEKHA_QDRANT_COLLECTION=lekha_chunks_minilm
```

Restart the API and worker, then re-upload documents (vectors from different models can't share
a collection; the app refuses a mismatch). The weights file is checked against a pinned SHA-256.
Kannada and Hindi need BGE-M3. Tests always use the fake embedder, whatever `.env` says.

## BGE-M3 (multilingual, approved)

About 2.3 GB, run with ONNX Runtime (no PyTorch). Download once (resumable; on slow links
set `HF_HUB_DISABLE_XET=1` so progress is saved to disk):

```
uv run hf download BAAI/bge-m3 --include "onnx/*" --local-dir models/bge-m3
```

Then in `.env`: `LEKHA_EMBEDDER=bge-m3` and `LEKHA_QDRANT_COLLECTION=lekha_chunks_bge_m3`,
restart, and re-upload. Files are checked against the repository's SHA-256 at startup.
`LEKHA_EMBEDDER_DEVICE=cuda` uses the GPU only if a CUDA-enabled ONNX Runtime is installed;
otherwise it runs on the CPU and logs a warning.

## Asking questions (Phase 3)

`POST /api/v1/ask` answers from documents the caller may read, with verified citations, or
says it doesn't know. It needs a language model:

- **Local (development):** install [Ollama](https://ollama.com) and pull a model; this
  laptop already has `qwen3.5:9b` (6.6 GB). In `.env`: `LEKHA_LLM=ollama`. The address must
  be `127.0.0.1` and cloud model tags are refused, so no text leaves the machine.
  Expect about 20–40 s per answer on the RTX 3050 (the model doesn't fit in 4 GB of VRAM).
- **Production:** `LEKHA_LLM=bedrock` (Claude via AWS Bedrock Mumbai), blocked until the
  AWS account exists.
- **None** (default): `/ask` answers `503 upstream_unavailable`.

```bash
curl -H "X-Dev-User: dev|asha" -H "Content-Type: application/json" -d '{"question":"How many days of annual leave do I get?"}' http://127.0.0.1:8000/api/v1/ask
```

`/ask` is rate-limited per user (`LEKHA_ASK_RATE_LIMIT_PER_MINUTE`, default 10).
`./dev.ps1 eval` runs the 100-question golden set with BGE-M3 and Ollama and writes
`docs/eval/golden-ollama-bge-m3.md` (about an hour).

## Web app (Phase 4 demo)

```powershell
./dev.ps1 web          # http://127.0.0.1:5173 (the API must be running)
./dev.ps1 web-check    # type-check + unit tests
./dev.ps1 demo-load    # load 42 synthetic policies (API and worker running)
```

Node.js 24 is required. `frontend/.npmrc` pins every package to a release published before a
cut-off date (like uv's `exclude-newer`), with one documented security exception (Vite).
The leadership walkthrough is in [demo-script.md](demo-script.md).

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
