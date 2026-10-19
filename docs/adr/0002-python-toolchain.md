# ADR-0002: Python 3.12 toolchain (uv, lockfile, quality gates)

- **Status:** Proposed
- **Date:** 2026-10-17

## Context
CLAUDE.md requires Python 3.12, a pinned dependency set with a lockfile, and running a
formatter, linter, type checker, and tests on every change. The laptop's default Python
is 3.14; 3.12 was available only through uv.

## Decision
- **uv** manages the interpreter (`.python-version` = 3.12), the virtual environment, and
  `uv.lock`. Every `dev.ps1` command runs `uv run --locked`, so a stale lock fails loudly.
- `[tool.uv] exclude-newer` ignores packages published after a fixed date (currently
  2026-10-01). This guards against freshly published malicious releases. Bump it deliberately.
- Quality gates: `ruff format`, `ruff check` (including the bandit-style `S` security
  rules), `mypy --strict` with the Pydantic plugin, `lint-imports`, and `pytest` with
  warnings treated as errors.
- Build backend: hatchling.

## Alternatives considered
- **Poetry**: mature, but slower, and it does not manage Python versions.
- **pip + pip-tools**: works, but has more moving parts on Windows.
- **Black + flake8 + isort**: ruff replaces all three with one fast tool.

## Why it fits Lekha
Reproducible builds on Windows and Linux from one lockfile; contributors do not need to
install Python manually.

## Trade-offs
uv is younger than pip and Poetry. `exclude-newer` delays urgent security patches until
the date is bumped (on purpose).

## Security and operational implications
The lockfile pins hashes; the cut-off date reduces supply-chain exposure.

## Cost
Free and open source.

## Revisit when
The organisation mandates a different package manager or an internal package mirror.
