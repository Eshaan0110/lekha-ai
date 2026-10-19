# ADR-0001: Modular monolith with clean layering

- **Status:** Accepted (part of the approved CLAUDE.md baseline, §4)
- **Date:** 2026-10-17

## Context
Lekha needs strong boundaries (auth, ingestion, retrieval, generation, audit…) but has a
small team and no operational need yet for independent scaling.

## Decision
One deployable Python application. Business modules live under `lekha/`, entry points
under `apps/`, persistence under `db/`. Dependencies point inward: `apps → lekha/db`,
never the reverse. The rules are enforced automatically by `import-linter` in
`./dev.ps1 check`.

## Alternatives considered
- **Microservices**: independent deploys, but network calls, distributed transactions,
  and many more things to secure and operate. Premature.
- **Unstructured monolith**: fastest start, but boundaries erode and later extraction is costly.

## Why it fits Lekha
Security reviews and authorization tests are simpler with one process and one trust
boundary to the database. Module boundaries keep the option to extract later.

## Trade-offs
All modules scale together; a bad deploy affects everything; boundaries depend on
discipline plus the import linter.

## Security and operational implications
One service to harden, monitor, and patch. Import contracts stop business code from
reaching into HTTP-layer state.

## Cost
Lowest: one container image, one deployment.

## Revisit when
A module has a clearly different scaling, availability, or data-residency requirement
(for example speech processing in Phase 5).
