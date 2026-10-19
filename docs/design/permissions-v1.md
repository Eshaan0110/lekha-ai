# Permissions design v1 (who can see which document)

- **Status:** Approved for implementation by the owner on 2026-10-18. §2–§8 and §10 are
  implemented and tested (slices 2–3), and §9 (audit log, slice 4) except the confidential-upload block (§5, with uploads) and the
  audit reader endpoint, which follow in later slices.
- **Date:** 2026-10-18
- **Sources:** [`docs/implementation-plan/`](../implementation-plan/): *Lekha — Implementation Plan* (16 Oct 2026) and *Lekha Production-Grade
  Technical Architecture and Implementation Roadmap*, §"Data model", "Tenant and
  business-unit isolation", "Retrieval", ADR-006 ("Permission tags in Qdrant payload").
  This design follows those documents. Where it adds or changes something, it says so
  under **"Gap found"** or **"Proposed addition"**.

## 1. The idea in one paragraph

Every document version has a **guest list** (its ACL). Every signed-in user has a set of
**badges**: their user ID, their roles in each business unit, and their IdP groups. A user
can read a document version only if **one of their badges is on its guest list**, **and**
they belong to the right business unit, **and** they are cleared for its sensitivity level.
If any part is missing or unclear, the answer is **no** (fail closed). The same rule is
applied inside the database query and the vector search, so forbidden text is never
fetched at all. It is never fetched first and then hidden.

## 2. The building blocks (from the plan)

| Thing | Plain meaning | Table |
|---|---|---|
| Organisation | The whole company (Manipal Group). Called the "tenant". | `organisation` |
| Business unit (BU) | One Manipal business | `business_unit` |
| User | A person who signs in through the Group IdP | `user` (`idp_subject` unique) |
| Role | A named bundle of powers | `role` |
| Role grant | "User X has role Y **in BU Z**" | `user_role_grant` |
| Permission subject | A badge that can go on a guest list | `permission_subject` |
| Document | A real-world thing: a policy, a contract summary, … | `document` |
| Document version | One frozen snapshot of a document | `document_version` |
| Document file | The stored bytes plus checksum | `document_file` |
| Document ACL | The guest list for one version | `document_acl` |
| Classification | Sensitivity: `public` < `internal` < `confidential` < `restricted` | enum on `document` |

Roles from the plan: `viewer`, `curator`, `admin`, `legal`, plus `audit_reader` (needed by
`GET /api/v1/audit`).

## 3. The access rule

A user may **read** a document version only if **all** of these are true:

1. **Same organisation.** The document's `organisation_id` equals the user's.
2. **Right business unit.** The document's BU is one the user has a role grant in.
3. **Cleared for its sensitivity.** The document's classification is at or below the
   user's clearance *for that BU*.
4. **On the guest list.** The version's ACL contains at least one of the user's badges,
   and that ACL entry has not expired.
5. **Still live.** The version is not deleted, and it is the current version (a curator may
   explicitly ask for an older one).

There is no "allow everything" shortcut and no admin bypass for reading content. Admins
manage users and roles; they still need to be on a guest list to read a document.

### One rule, three places
The rule is written **once**, as a pure Python function in `lekha/auth` (no database or
framework code). From it we build:
- the **Postgres** query filter (document lists, downloads);
- the **Qdrant** payload filter (search, Phase 2/3);
- the in-memory check used by tests.

Tests check that all three give the same answer for every user/document pair in a test
matrix.

## 4. Badges (permission subjects)

| Badge | Example | Comes from |
|---|---|---|
| User | `user:7f3c…` | The user's own ID |
| Role in a BU | `role:<bu_id>:<role_id>` | `user_role_grant` |
| IdP group | `group:bu-chennai` | Group claims from the IdP (dev adapter locally) |

> **Gap found: role badges must include the business unit.**
> The plan's examples write roles as `role:hr-reader`, without a BU. Picture this:
> - Asha has `hr-reader` in BU **A** and `viewer` in BU **B**.
> - Search filters on "badge `role:hr-reader` **and** BU in {A, B}".
> - A BU **B** document whose guest list includes `role:hr-reader`, meant for B's HR team,
>   would then be visible to Asha. That is a cross-business-unit leak.
>
> **Fix (implemented):** every role badge includes its BU: `role:<bu_id>:<role_id>`. IDs are
> used instead of role names, so renaming a role never breaks existing guest lists.
> A database check constraint rejects any role badge without its BU, and any badge whose
> key doesn't match its own IDs.
>
> **Note on cross-unit invitations.** A document's guest list may name a badge from another
> BU (e.g. a beta document listing `role:<alpha>:viewer`). That alone grants nothing: the
> reader must *also* hold a role in the document's own BU (rule 2) and be cleared for it
> (rule 3). Tested in `test_access_rule.py` and by seed case B2.
> A dedicated test covers exactly this scenario.

## 5. Clearance

> **Addition (implemented).** The plan says retrieval filters on "classification within the
> user's clearance" but doesn't say where clearance is stored. Each **role** carries a
> `max_classification` (for example `viewer` → `internal`, `legal` → `confidential`). A
> user's clearance in a BU is the highest across their role grants there. No role reaches
> `restricted` until the restricted-data path (Sarvam / on-prem) exists.

> **Proposed safeguard (release gate in code).** Until the Phase 2 release gate is formally
> passed, uploads classified `confidential` or `restricted` are **rejected** by the API,
> controlled by a setting that defaults to "off". This turns the plan's rule "no
> confidential data until the gate passes" into something code enforces.

## 6. Who you are on each request (AuthContext)

On every authenticated request we build one frozen `AuthContext`:
`user_id`, `organisation_id`, `badges`, `bu_scope`, `clearance_by_bu`, `roles_by_bu`.

- It is built **from the database** on each request (roles can change; the IdP token
  alone is not trusted for roles).
- It is passed **explicitly** to every function that touches documents (plan rule:
  "Every cross-module call that crosses a trust boundary passes AuthContext explicitly").
- No AuthContext → `401 unauthenticated`. Empty badges → the user sees nothing, without
  an error.
- A document the user can't see returns `404 not_found`, not 403, so its existence is not
  revealed (the plan defines 404 as "Not found or not visible").

## 7. Signing in locally (dev-only adapter)

The real Group OIDC sign-in is **blocked** (Phase 0: IdP not ready). For local work:

- A `DevAuthAdapter` reads an `X-Dev-User: <seed-user-key>` header and loads that
  **synthetic** seeded user.
- It **refuses to load** unless `LEKHA_ENVIRONMENT` is `local` or `test`. The app also
  refuses to start in staging/production if the adapter is configured. Both are tested.
- Seed data: two BUs and about five fake users covering the test matrix (same-BU reader,
  other-BU reader, curator, user with no grants, expired ACL entry).
- When OIDC arrives it plugs into the same interface. Per the plan, it will use
  server-side sessions in Redis with HTTP-only cookies.

## 8. Defence in depth in Postgres

From the plan's "Tenant and business-unit isolation":

- **Row-level security (RLS)** on every tenant table, using
  `current_setting('lekha.tenant_id')`, set at the start of each request's transaction.
- **Least-privilege database roles** (already on our next-steps list): a migration-owner
  role and a separate app role with only the grants it needs. The app role does not own
  the tables, so RLS applies to it.

> **Proposed scope for v1:** RLS enforces the **organisation** boundary. The finer rule in
> §3 (BU + clearance + guest list) is enforced by the shared filter from §3. Putting the
> full rule into RLS policies too would mean maintaining it in two languages; we can add
> that later if the security review asks for it.

## 9. Audit log (append-only)

`audit_event`: `id`, `occurred_at`, `request_id`, `organisation_id`, `actor_user_id`,
`action` (e.g. `auth.sign_in`, `document.upload`, `document.read`, `search`,
`access.denied`), `target_type`, `target_id`, `outcome`, `metadata` (JSONB with **no**
document text, tokens or personal data).

- The app database role may only **INSERT** and **SELECT**. A trigger blocks UPDATE and
  DELETE, even for accidental code paths.
- Reading it needs the `audit_reader` role and is limited to the caller's BU (from the plan).
- WORM export to S3 Object Lock is a production item.

## 10. Tests that must pass before this is called done

- Cross-BU isolation, including the role-badge leak scenario in §4.
- Clearance: an `internal`-cleared user cannot see a `confidential` document, even if on
  its guest list.
- An expired ACL entry grants nothing; a deleted version grants nothing.
- A user with no grants sees nothing.
- Missing or unknown dev user → 401; dev adapter refused in production.
- The Postgres filter and the in-memory rule agree on the whole test matrix.
- RLS: a request scoped to one organisation cannot read another organisation's rows,
  even with hand-written SQL.
- The audit table rejects UPDATE and DELETE.

## 11. Out of scope for v1

`comment` / `curate` access levels are stored but not used yet. Also out of scope: ACL
inheritance from source systems (SharePoint etc., Phase 6), SCIM sync, and the real
OIDC flow.

## 12. Other differences noticed in the plan (to fix separately)

| Plan says | Built so far | Proposed action |
|---|---|---|
| Error envelope `{"error": {"code", "message", "detail": {...}}}` | `details` list + `request_id` in the body | **Done 2026-10-19:** exact plan shape; request ID in the `X-Request-ID` header only |
| 503 code `upstream_unavailable` | `service_unavailable` | Rename to match the plan |
| Settings at `lekha/config.py` | `lekha/core/config.py` | Keep (documented deviation), or move; minor |
| Local storage: MinIO | SeaweedFS (ADR-0005, approved) | None |
