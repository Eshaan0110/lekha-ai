# Lekha

Lekha is a shared enterprise knowledge and memory platform for Manipal Group employees.
Employees will ask questions about approved company documents and get answers that are
grounded in evidence, cite exact sources, respect the caller's permissions, and abstain
when the evidence is insufficient.

> **Status:** Phase 1 (secure foundation) — *in progress, local only*. Nothing here is
> production-ready. See [docs/phase-tracker.md](docs/phase-tracker.md).

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12
for you), Git.

```powershell
./dev.ps1 setup             # install locked deps, generate .env with random local secrets
./dev.ps1 up                # start PostgreSQL, Redis, Qdrant, object storage (localhost only)
./dev.ps1 migrate           # apply database migrations + create the API's limited DB login
./dev.ps1 seed              # load the SYNTHETIC dev organisation and users
./dev.ps1 run               # API on http://127.0.0.1:8000  (docs at /docs)
./dev.ps1 check             # format, lint, types, import boundaries, unit tests
./dev.ps1 test-integration  # tests against the running stack
./dev.ps1 down              # stop containers (data is kept)
```

Full guide: [docs/local-development.md](docs/local-development.md).

## Documentation

| Document | Purpose |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Master engineering instructions (governing) |
| [docs/phase-tracker.md](docs/phase-tracker.md) | Phase status, implementation status, external dependencies |
| [docs/architecture.md](docs/architecture.md) | Module boundaries, layers, request flow |
| [docs/security.md](docs/security.md) | Security controls: implemented vs. pending |
| [docs/local-development.md](docs/local-development.md) | Running and testing locally |
| [docs/adr/](docs/adr/) | Architecture Decision Records |

## Data rule

Use **synthetic, non-confidential** documents only. Do not put real company data, real
credentials, or personal information into this repository or the local stack.
