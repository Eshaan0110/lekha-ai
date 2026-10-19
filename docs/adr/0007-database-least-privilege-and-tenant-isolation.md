# ADR-0007: Least-privilege database account and tenant isolation in PostgreSQL

- **Status:** Accepted. Part of permissions design v1, approved for implementation by the owner on 2026-10-18.
- **Date:** 2026-10-18

## Context
Slice 1 connected the API as the database owner, which could create or drop any table and
bypass every row-level rule. The Implementation Plan (Technical Architecture §"Tenant and
business-unit isolation") requires defence in depth: `organisation_id` on every row,
Postgres row-level security reading `current_setting('lekha.tenant_id')`, and permission
filters on every query.

## Decision
1. **Two accounts.** The owner account (`LEKHA_POSTGRES_USER`) runs migrations only. The API
   connects as `LEKHA_DB_APP_USER`, a login that belongs to the group role `lekha_app_rw`:
   no superuser, no `BYPASSRLS`, no DDL, and no writes to `organisation`. Settings refuse to
   start if both accounts have the same name. The login and its password are created by
   `apps/scripts/provision_db_app_user.py`, never by a migration.
2. **Row-level security** on all 11 tables. The policy compares `organisation_id` with
   `lekha_current_tenant()`, which returns NULL when unset, so an unscoped transaction sees
   **nothing**. The tenant is set per transaction (`set_config(..., true)`) by
   `db.tenancy.tenant_transaction`, so it cannot leak to the next user of a pooled
   connection.
3. **Composite foreign keys** `(organisation_id, x_id) → parent(organisation_id, id)`, so a
   row can never reference another tenant's row, even when written by the owner account.
4. **Badge integrity in the database.** Check constraints force each permission-subject key
   to match its own IDs, and force role badges to include their business unit.
5. **No SQL values in errors.** Engines use `hide_parameters=True`, and provisioning replaces
   failing DDL errors with a sanitised message.

## Alternatives considered
- **Application-only filtering**: simpler, but one missed `WHERE` clause leaks data across tenants.
- **Full ACL logic in RLS policies**: strongest, but it duplicates the permission rule in
  SQL and Python and is hard to test. Deferred; the finer rule is enforced by one shared filter.
- **Database per tenant**: the strongest isolation, but heavy to operate for one organisation today.
- **Password set inside a migration**: it would put secrets into migration history and logs.

## Why it fits Lekha
"Permissions enforced at search time" (plan rule 1) needs layers that hold even when
application code has a bug. Each layer here is proven by an integration test.

## Trade-offs
- `organisation_id` is repeated on every table, and foreign keys become composite (wider indexes).
- Every request must open a tenant transaction; forgetting to do so fails closed (empty
  results) rather than open.
- Roles are server-wide, so downgrading migration 0001 removes its grants but keeps the role.

## Security and operational implications
Production must create the app login through infrastructure tooling with a password from
the secrets manager. RLS does not apply to the owner, so the owner account must never be
given to the application.

## Cost
None.

## Revisit when
A second organisation joins (consider a collection per organisation in Qdrant, as the plan
says), or the security review asks for the full ACL rule to move into RLS.
