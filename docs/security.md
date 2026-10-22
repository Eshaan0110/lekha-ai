# Security notes

This file separates controls that **exist and are tested** from those that are
**placeholders** or **production prerequisites**. A control is not claimed until a test or
a recorded manual check proves it.

## Implemented and tested (Phase 1, slice 1)

| Control | How | Evidence |
|---|---|---|
| No secrets in the repository | `.env`, generated credentials, and backups are git-ignored; `.env.example` holds placeholders only | `.gitignore`; `git status` review |
| Strong local secrets | `dev.ps1 setup` generates 32-byte CSPRNG secrets | Manual check (values redacted) |
| Fail-closed configuration | Missing, placeholder (`CHANGE_ME`), or <16-character secrets stop startup; error never echoes the value | `tests/unit/test_config.py` |
| Secrets masked in memory dumps | Pydantic `SecretStr` | `test_secrets_are_masked_in_repr` |
| Staging/production guards | JSON logs required; DEBUG forbidden | `test_config.py` |
| Every service requires credentials | Postgres SCRAM password, Redis `requirepass`, Qdrant API key, S3 identity | Manual probes: Qdrant 401 without key, Redis `NOAUTH`, S3 403 anonymous; integration test for wrong credentials |
| Services not exposed to the network | Every port bound to `127.0.0.1`; `no-new-privileges` | `docker-compose.yml`; `netstat` check |
| No internal details in API errors | Common error envelope; generic 500; validation errors omit submitted values | `test_api.py` |
| No sensitive data in logs | Paths logged without query strings; HTTP-client URL logging silenced; readiness logs exception *type* only | `test_logs_are_json_correlated_and_free_of_secrets` |
| Log-injection-safe request IDs | Incoming `X-Request-ID` accepted only if `^[A-Za-z0-9._-]{1,64}$` | `test_unsafe_or_missing_request_id_is_replaced` |
| Timeouts on every dependency call | DB connect 5 s + `statement_timeout` 15 s; Redis 2/5 s; HTTP 2/5 s; readiness 2 s per check | `test_hanging_dependency_times_out_instead_of_blocking`; `test_engine_applies_statement_timeout` |
| Supply-chain hygiene | Locked dependencies (`uv.lock`), `exclude-newer` cut-off, exact image tags | `pyproject.toml`, compose file |
| Least-privilege database account | API connects as `lekha_app` (no DDL, no superuser, no `BYPASSRLS`, cannot create organisations); owner used only for migrations | `test_app_role_*` in `test_schema_security.py` |
| Tenant row-level security | RLS on all 11 tables; an unscoped transaction sees zero rows; the tenant setting lasts one transaction | `test_unscoped_app_transaction_sees_no_tenant_rows`, `test_tenant_scope_does_not_outlive_the_transaction`, `test_cannot_write_rows_into_another_tenant` |
| No cross-tenant references | Composite `(organisation_id, id)` foreign keys | `test_acl_cannot_point_at_another_tenants_document` |
| Badge integrity | Check constraints: a key must match its IDs; role badges carry their BU | `test_role_badge_must_name_its_business_unit`, `test_user_badge_cannot_claim_a_different_user` |
| No SQL values or secrets in DB errors | `hide_parameters=True`; sanitised provisioning errors | Found and fixed in slice 2 (a provisioning error printed a local password; it was rotated) |
| Authentication fails closed | No identity provider, missing/invalid credentials, unknown or inactive user → `401`, same message every time | `test_protected_routes_refuse_everyone_while_oidc_is_unavailable`, `test_inactive_and_unknown_users_cannot_sign_in` |
| Dev sign-in is local-only | `auth_mode=dev` refused by settings and by the adapter outside `local`/`test`; only `dev|…` subjects accepted | `test_dev_auth.py` |
| Permission rule enforced in the query | Organisation + BU role + clearance + unexpired guest-list entry + live current version, as SQL; forbidden rows never leave the database | `test_api_isolation.py`, `test_sql_filter_agrees_with_the_reference_rule` |
| Existence not revealed | Invisible and missing documents both return `404 not_found` | `test_opening_a_document_directly_obeys_the_same_rule` |
| No personal data in logs | Logs carry the internal user ID only, never names or IdP subjects | `apps/api/auth.py` |
| Audit trail for every API request | One `audit_event` per `/api/` request: request ID, actor, organisation, action, target, status, outcome; no content, names, tokens or query strings | `test_every_api_request_is_audited_with_correlation`, `test_signed_in_request_is_recorded_with_actor_and_target` |
| Audit is fail-closed | Event written before the response starts; if it fails, the caller gets a generic 500 and no data | `test_data_is_withheld_when_the_audit_cannot_be_written` (real DB, insert revoked) |
| Audit is append-only | UPDATE/DELETE/TRUNCATE revoked from the app role **and** blocked by trigger for every role, including the owner | `test_app_role_cannot_change_or_remove_events`, `test_even_the_owner_cannot_change_or_remove_events` |
| Audit tenant isolation | Tenants read only their own events and cannot forge events for another tenant | `test_tenants_read_only_their_own_events`, `test_a_tenant_cannot_write_events_for_another` |
| Upload type checking | PDF/DOCX decided from file contents; extension must agree; macro-enabled Word refused; DOCX size/entry bounds | `test_upload_validation.py`, `test_rejected_uploads_store_nothing` |
| Upload size limit | Checked from `Content-Length` before reading, then on the bytes | `test_oversized_upload_is_refused_before_the_body_is_read` |
| Who may upload | Active curator in the business unit, classifying within their own clearance | `test_upload_rule`, `test_rejected_uploads_store_nothing` |
| Confidential-data release gate | `confidential`/`restricted` uploads refused by default; cannot be enabled in local/test | `test_upload_rule`, `confidential gate` integration case |
| Storage integrity and layout | SHA-256 verified by storage (`BadDigest` on mismatch); keys contain only IDs under org/BU prefixes; filenames sanitised | `test_curator_upload_is_stored_recorded_and_visible_to_its_guests`, `test_filenames_are_sanitised` |
| No orphaned files | Rejected uploads store nothing; a failed DB write deletes the stored object | `test_losing_a_race_removes_the_stored_file`, `test_storage_outage_fails_cleanly_with_no_rows` |
| Verified backups | One folder per backup with SHA-256 of every file; written as `.partial` until complete; restore verifies everything first and changes nothing on any mismatch or path escape | `test_full_restore_drill`, `test_a_damaged_backup_is_refused_before_anything_changes`, `test_manifest_paths_cannot_escape_the_backup_folder` |
| Safe retries | `Idempotency-Key`: a retried upload replays the original response instead of duplicating; keys are private to each user; reuse for a different request or while in progress is `409`; failed attempts free the key; concurrent claims admit exactly one | `test_idempotency.py` |
| Permission-filtered search | Qdrant filter built only from AuthContext (tenant, BU, clearance, `allowed_subjects`, current version); searches without it are refused; every hit re-checked against the database rule | `test_ingestion_search.py`, `test_ingestion.py` |
| Deletion | Only curators of the unit, cleared for the classification; the document is hidden in the same transaction that records the request; vectors, chunks and files are purged; tombstone and audit event kept; non-curators who can read get 403, others 404 | `test_deleting_a_document_hides_it_at_once_then_purges_everything`, `test_who_may_change_or_delete_a_document` |
| Versions can't widen access to old versions | A new version must keep the document's unit and classification; it becomes current only after indexing | `test_a_new_version_cannot_change_who_may_read_the_old_ones`, `test_a_new_version_takes_over_only_once_it_is_indexed` |
| Model integrity | Embedding model files are checked against pinned SHA-256 before loading | `test_unexpected_weights_are_refused`, `test_bge_m3_refuses_files_that_fail_their_checksum` |
| Grounded answers only | The model sees only permission-filtered chunks, fenced as data; every citation's quote must appear in its source; no verified citation → abstain; no/weak evidence → abstain without calling the model | `test_ask.py`, `test_generation.py` |
| Answer logs | Question and answer text stored in `query_log`/`answer_log` (tenant RLS), never in application logs; answers quoting a deleted document are wiped | `test_deleting_a_document_wipes_logged_answers_that_quoted_it` |
| Local model stays local | Ollama URL must be loopback; cloud model tags refused; Ollama refused in staging/production | `test_config.py` |
| Abuse and cost control | `/ask` limited per user per minute; Redis outage refuses rather than lets through | `test_rate_limits_fail_closed`, `test_the_redis_limiter_counts_per_user` |
| Meetings readable only by participants | A meeting's guest list is its participants (importer always included); unit curators who weren't present can't search, list, open or ask about it | `test_only_participants_can_find_read_or_ask_about_a_meeting` |
| Transcript validation | UTF-8 only, ≤ 5 MB, ≤ 20,000 turns, markup stripped, unknown formats refused | `test_transcript.py`, `test_import_rules` |
| Minutes are human-approved and grounded | Drafts are never searchable; only the organiser approves; every decision and action must quote the transcript verbatim or it is dropped; nothing is sent anywhere | `test_minutes.py`, `test_minutes_are_drafted_by_participants_and_approved_only_by_the_organiser`, `test_invented_evidence_never_reaches_the_approver` |
| Glossary can't widen access | Entries are managed by the unit's curators and visible per unit (or shared); expansion only changes the query, every search stays permission-filtered; entry text sent to the model is neutralised | `test_glossary.py` (Ravi gets no expansion or results from Alpha's entry) |
| Audit reader | `GET /api/v1/audit` requires `audit_reader`; scoped to activity by people with a role in the reader's unit; shows actions and paths, never question or document text | `test_the_audit_log_is_for_audit_readers_and_scoped_to_their_unit` |
| Question requests need consent | A question reaches curators only if the asker sends it; curators of other units can't see or close it | `test_an_unanswered_question_can_be_logged_and_followed_up_by_curators` |
| Instruction-like text is never evidence | Sentences addressed to an AI ("ignore the user's question", "system instruction", "Assistant, …") can't be cited in answers or minutes, even when quoted exactly | `test_injection.py`; golden probe X09 |
| Interactive API docs off outside local/test | `/docs` and `/openapi.json` disabled in staging/production | `test_interactive_docs_are_disabled_in_production` |

## Not yet implemented (Phase 1, later slices)

- Group OIDC sign-in (blocked on Phase 0). Until then every protected route answers 401 outside local/test.
- WORM export and retention for the audit log (the reader endpoint is built, Phase 4).
- Malware scanning of uploads.
- Rate limiting (planned phase per Implementation Plan).

## Production prerequisites (not satisfied; do not deploy)

- Group OIDC integration and security review.
- Secrets from AWS Secrets Manager or Vault, never `.env`.
- TLS everywhere; health endpoints reachable only from the internal network.
- Data-residency verification (AWS Mumbai), DPDP legal review, processor contracts.
- Monitoring and alerting (Prometheus/Grafana/Loki/Langfuse).
- Tested backup/restore with off-host copies and a recovery-time objective.

## Rules for contributors

- Use synthetic documents only. Never paste real company data, tokens, or personal data
  into code, tests, logs, screenshots, or issues.
- Never weaken or bypass a security check to make a test pass.
