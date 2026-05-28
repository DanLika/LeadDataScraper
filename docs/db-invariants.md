# Supabase DB invariant gates

Extracted from `CLAUDE.md` (2026-05-26 shrink; original ~164k chars). Restored to docs/ to keep CLAUDE.md under the harness threshold without losing content.

- **Supabase schema + RLS drift gate**
  (`src/scripts/schema_drift_check.py`). Runs in both `ci.yml`
  (PR-time, blocks merge) and `security.yml` (push to `main` + daily
  cron — catches manual Supabase Studio edits between PRs). Connects via
  the `SUPABASE_DATABASE_URL` GitHub Actions secret (`?sslmode=require`).
  Fail-closed: exits 2 if the secret is unset, so a missing/typo'd
  secret turns the job red instead of silently passing. Asserts: column
  parity vs `supabase_schema.sql` (CREATE TABLE + ALTER TABLE ADD
  COLUMN, no missing/extra); RLS enabled on `leads`, `campaigns`,
  `campaign_messages`, `orchestration_jobs`; a `<table>_deny_all` policy
  exists on each (roles ⊇ {anon, authenticated}, FOR ALL, qual=false,
  with_check=false); no anon/authenticated/PUBLIC GRANT on those 4
  tables; `add_lead_column` is SECURITY DEFINER, owned by `postgres`,
  has `search_path` set, and no EXECUTE grant to
  anon/authenticated/PUBLIC. Column check is **name-only** — type drift
  (e.g. `needs_manual_review` text-vs-boolean, `outreach_score`
  double-vs-int) is intentionally out of scope and tracked separately.
- **Supabase referential integrity gate**
  (`src/scripts/check_referential_integrity.py`). Runs alongside the
  drift gate in both workflows. Exercises invariants that a static
  schema check can't prove: (1) deleting a `campaigns` row CASCADE-deletes
  its `campaign_messages` children; (2) inserting a `campaign_messages`
  row with a non-existent `lead_unique_key` raises
  `ForeignKeyViolation`. All mutations run inside a single transaction
  that is **unconditionally rolled back** in a `finally` block — Postgres
  READ COMMITTED hides the in-flight rows from other sessions, and ROLLBACK
  undoes everything even if the connection drops mid-test. UUID IDs prevent
  collisions between concurrent CI runs. Shares the `SUPABASE_DATABASE_URL`
  secret with schema-drift, but the role must additionally have INSERT on
  `campaigns` + `campaign_messages` and DELETE on `campaigns`. The
  FK-violation probe runs inside a SAVEPOINT (`conn.transaction()`) so
  the outer rollback survives the inner abort.
- **Supabase hot-path index gate** (`src/scripts/check_query_plans.py`).
  Runs alongside the drift + integrity gates in both workflows. For each
  query in `HOT_PATH_QUERIES`, runs `SET LOCAL enable_seqscan = off`
  followed by `EXPLAIN (FORMAT JSON)`, walks the plan tree, and fails if
  any node is `Seq Scan`. Disabling seqscan forces the planner to pick
  any *usable* index — so the check works on empty tables (which the
  live DB currently has for `leads` and `campaign_messages`). Plain
  EXPLAIN ANALYZE on an empty table picks Seq Scan trivially regardless
  of indexes; `enable_seqscan=off` is the only way to distinguish "no
  index" from "no rows yet". Covers: dashboard `ORDER BY created_at
  DESC LIMIT 200`, `WHERE audit_status = ?`, `WHERE unique_key = ?`,
  and `campaign_messages WHERE campaign_id = ?`. Read-only role is
  sufficient because EXPLAIN without ANALYZE never executes the query
  body. The supporting indexes (`idx_leads_created_at_desc`,
  `idx_leads_audit_status`, `idx_orchestration_jobs_status`) were
  reconciled in migration `add_missing_perf_indexes` (declared in
  `supabase_schema.sql` but missing from the live project — verified
  via `pg_indexes` on 2026-05-22). The redundant
  `idx_leads_unique_key` declaration was removed from the schema file —
  the UNIQUE constraint on `unique_key` auto-creates `leads_pkey` and a
  second named index would be write-amp on every INSERT.
- **DB-level CHECK constraints (defense in depth).** Supabase Studio
  and the `service_role` key both bypass Pydantic, so allowlist + range
  guards live in the database itself. Applied via the
  `add_check_constraints` migration and mirrored in
  `supabase_schema.sql` (under `DO $$ ... EXCEPTION WHEN
  duplicate_object` blocks — Postgres has no `ADD CONSTRAINT IF NOT
  EXISTS` for table-level CHECKs). `schema_drift_check.py` has an
  `EXPECTED_CHECK_CONSTRAINTS` allowlist + `check_check_constraints()`
  asserting parity in both directions (missing-in-DB **and**
  undeclared-in-schema).

  > **CHECK-allowlist pairing rule (PR #377).** Adding a CHECK to
  > `supabase_schema.sql` REQUIRES appending its name to the
  > `EXPECTED_CHECK_CONSTRAINTS` dict in `schema_drift_check.py` in
  > the SAME PR. The drift check compares live DB `pg_constraint`
  > names against the Python dict, NOT against parsed SQL. Forget
  > the dict update → every subsequent PR's `Schema drift + RLS
  > posture` job fails with "CHECK constraints present in DB but
  > not declared in schema", even though the SQL file declares
  > them. PRs #353/#356/#366 each fell into this trap. PR #377
  > retroactively appended the 6 missing names: `campaign_messages_bounce_reason_size`,
  > `webhook_events_event_id_size`, `sequence_steps_window_ordered`,
  > `sequence_steps_send_days_format`, `sequence_variants_body_size`,
  > `sequence_variants_content_type_allowed`.

  > **Regex / IN-list CHECK literal trap.** Body literals containing
  > apostrophes (regex anchors, `IN ('text','html')`-style lists) MUST
  > use the `E''`-prefix on the regex literal so Postgres' string
  > parser treats it as an escape-string. Without the prefix, any
  > apply path that re-wraps the SQL in single quotes (shell `psql
  > -c`, Management API SQL endpoint, etc.) doubles every internal
  > apostrophe and Postgres parses the result as a literal-apostrophe
  > pattern that rejects every legal value — including the column
  > default. Recovered as PR #366 + audit-trail migration
  > `scripts/migrations/2026-05-27_apostrophe-fix-and-leads-last-name.sql`.

  10 constraints currently locked in (pre-Phase-14/15; the seven
  Phase 14/15 hardening additions live in the `EXPECTED_CHECK_CONSTRAINTS`
  dict but are not enumerated here — see the dict for the canonical
  list):
  - `leads_seo_score_range` / `leads_outreach_score_range` — 0..100
    inclusive, NULL allowed.
  - `leads_audit_status_allowed` — wide allowlist matching producer
    reality: `'Pending'`, `'Processing'`, `'Completed'`, `'Failed'`,
    plus error-reason strings `'Timeout'`, `'403 Forbidden'`,
    `'404 Not Found'`, `'Invalid URL'`. The last four are misuse of
    the `audit_status` slot (a separate `last_error TEXT` column
    exists for reasons); refactoring `src/core/parallel_auditor.py`
    to write only the four canonical statuses would let us shrink the
    allowlist. Tracked as future cleanup.
  - `leads_enrichment_status_allowed` — uppercase per
    `src/scrapers/enrichment_engine.py`:
    `'PENDING'`/`'COMPLETED'`/`'FAILED'`/`'FAILED_NO_CONTENT'`.
  - `leads_email_basic_shape` — `email IS NULL OR
    (length(email) >= 3 AND email LIKE '%@%')`. Loose by design —
    the strict regex lives at the SMTP boundary in
    `src/integrations/email_sender.py`; DB only rejects obviously
    broken values so scraped imports don't fail on quirky-but-valid
    addresses.
  - `orchestration_jobs_status_allowed` —
    `'starting'`/`'running'`/`'completed'`/`'failed'`/`'stopped'`.
  - `campaigns_channel_allowed` +
    `campaign_messages_channel_allowed` —
    `'email'`/`'linkedin'`/`'multi'`.
  - `campaigns_status_allowed` —
    `'draft'`/`'active'`/`'paused'`/`'completed'` (last is
    forward-compat; no producer writes it yet).
  - `campaign_messages_status_allowed` —
    `'pending'`/`'sent'`/`'delivered'`/`'replied'`/`'bounced'` (only
    `'pending'` written today; the rest forward-compat for SMTP /
    LinkedIn integration callbacks).
- **Supabase JSONB shape gate** (`src/scripts/check_jsonb_shapes.py`).
  Runs in `security.yml` on push + **daily cron only** — intentionally
  not PR-blocking. Shape drift in existing rows shouldn't block
  unrelated code merges; daily cadence is right for catching a Studio
  hand-edit or a producer-side regression that landed yesterday. Two
  columns validated:
  - `leads.audit_results` (only for `audit_status='Completed'` rows —
    Pending/Processing/Failed legitimately have NULL or partial
    payloads). Required keys + value types: `score` (number|null),
    `is_up` (boolean|null), `tech_flags` (object), `red_flags` (array).
    Producer: `src/scrapers/seo_audit.py::perform_seo_audit_async`
    persisted via `src/core/parallel_auditor.py`.
  - `orchestration_jobs.filters` must match **one of**
    `{"type": <str>}` (pipeline path in
    `task_orchestrator.py:143`) **or** `{"query": <str>,
    "location": <str>}` (discovery path in
    `task_orchestrator.py:101`). NULL is accepted.
  `business_details` + `contact_details` were originally listed
  alongside but are **TEXT free-form prose** (Gemini-generated, e.g.
  "Full-service plumbing company specializing in ..."), not JSONB —
  no structural validation possible. Promoting either to JSONB would
  be a separate, deliberate migration.
- **Supabase NULL ratio audit** (`src/scripts/check_null_audit.py`).
  Runs in `security.yml` on push + daily cron, but the per-human-review
  cadence is **weekly** — operator skims Monday's report to decide
  which CANDIDATE_DROP / TIGHTEN items become a real migration. Two
  failure modes: (1) advisory report (does NOT fail CI) — columns with
  >90% NULL ratio (drop candidates) and columns the app reads as
  required but the schema still allows NULL (`leads.name`,
  `leads.lead_source`, `campaigns.status`, `campaign_messages.status`,
  `campaign_messages.campaign_id`); (2) hard invariants (FAIL CI) —
  any NULL row in a column with a schema default + app guarantee
  (`unique_key`, `audit_status`, `created_at`, `updated_at` on
  `leads`; `name`, `channel`, `created_at`, `updated_at` on
  `campaigns`; `channel`, `created_at` on `campaign_messages`;
  `id`, `status`, `created_at`, `updated_at` on
  `orchestration_jobs`). Empty tables are skipped entirely — total=0
  would make every column trivially "0% NULL" of nothing, drowning
  the report. NULL counts are computed in one pass per table using
  `psycopg.sql.SQL` + `sql.Identifier` composition (column names from
  `information_schema`, never user input).
- **Supabase orphan + zombie sweep**
  (`src/scripts/check_orphans_and_zombies.py`). Runs in `security.yml`
  on push + daily cron. Five checks, ONE auto-heal:
  - **Soft-orphan campaign_messages** — `lead_unique_key` with no
    matching `leads.unique_key`. FK should prevent this; orphans
    signal a dropped or DEFERRABLE FK that the schema-drift gate
    should also catch.
  - **Zombie orchestration_jobs** — `status='running'` with
    `updated_at` older than `ZOMBIE_THRESHOLD_HOURS = 4`. **AUTO-HEALED**
    via `UPDATE orchestration_jobs SET status='failed',
    updated_at=now()`. This is the only auto-heal: low risk
    (slow-but-alive job at 4h is rare; flipping is reversible at zero
    cost), high value (unblocks the orchestrator from leaking the
    slot). All other checks involve user data where guessing wrong
    would destroy info.
  - **Stuck leads** — `audit_status IN ('Pending','Processing')` with
    `updated_at` older than `STUCK_THRESHOLD_HOURS = 24`. Report-only
    (could be retried, skipped, or reclassified — operator decides).
  - **State-machine violation** — `campaign_messages.sent_at IS NOT
    NULL AND status='pending'`. Report-only (don't know which write
    is wrong).
  - **Completed-without-results invariant** —
    `audit_status='Completed' AND audit_results IS NULL`. Report-only.
    Pairs with the JSONB shape gate.

  Role permission delta: the `SUPABASE_DATABASE_URL` Postgres role
  needs UPDATE on `orchestration_jobs` for the auto-heal (alongside
  the existing INSERT/DELETE perms on `campaigns`/`campaign_messages`
  that the referential-integrity gate needs). All other checks are
  pure SELECT.
- **Supabase concurrency / contention tests**
  (`tests/test_concurrent_writes.py`). Runs in a dedicated
  `concurrency-tests` job in `ci.yml` (PR-time, fork-PR guarded).
  Five tests on live DB; isolation via `_concurrency_test_<uuid>`
  unique-key prefix + per-test teardown + a session-scoped sweep
  fixture that wipes any leftover rows from a SIGKILL'd CI worker.
  Five invariants verified:
  - **20 concurrent UPDATEs** to the same lead converge under row-lock
    serialization — final `audit_status` is one of the values
    written, every UPDATE returns.
  - **20 concurrent INSERTs** with the same `unique_key` produce
    exactly 1 success and 19 `UniqueViolation`s (no torn rows, no
    deadlock).
  - **Concurrent UPDATE + DELETE** on the same row always converges
    to "row deleted" regardless of order (READ COMMITTED re-evaluates
    the WHERE clause on the loser).
  - **Lost-update window without advisory lock** — documents that
    READ COMMITTED does NOT prevent classic read-modify-write losses
    between two writers. Assertion is intentionally weak ("final
    value is one of the writers"); a stronger invariant requires an
    application-level serialization layer.
  - **`pg_advisory_xact_lock` serializes 20 read-modify-write
    increments** — final value is exactly `initial + 20`. Documents
    the fix to adopt in `ParallelAuditor` when a lead can race with a
    manual UI edit. Lock key: `(LEAD_LOCK_NAMESPACE=0x4EAD,
    hashtext(unique_key))` — the namespace constant MUST be reused
    by any other code that locks on a lead.
  The unit-test job (`python-tests`) also collects this file but
  every test skips via `pytest.importorskip("psycopg")` +
  `pytest.mark.skipif(not DATABASE_URL, ...)` since `requirements.txt`
  doesn't include psycopg. So the test file is exercised only in the
  dedicated job with the right env.
- **Per-role `statement_timeout` (long-running query DoS guard).**
  Defaults configured at the role level via `ALTER ROLE ... SET
  statement_timeout = ...` so every new connection inherits the cap:
  - `anon` → **3s** (Supabase default, kept tight)
  - `authenticated` → **8s** (Supabase default, kept tight)
  - `service_role` → **30s** (added via `set_service_role_statement_timeout`
    migration — Supabase ships this role with no timeout). Generous
    enough for the longest legitimate single statement on the
    pipeline's hot paths, tight enough to abort any runaway.
  Verified daily in `security.yml` by
  `src/scripts/check_statement_timeouts.py`. Two layers: (1) query
  `pg_db_role_setting` and assert each role carries the expected
  `statement_timeout=Ns` entry — catches a "RESET ALL" or
  ALTER-ROLE-undone via Studio; (2) prove the cancellation primitive
  fires by `SET LOCAL statement_timeout = '2s'` followed by
  `SELECT pg_sleep(5)` — must raise `QueryCanceled`. Together these
  transitively verify per-role behavior without needing separate
  per-role connection strings. **Optional**: set
  `DATABASE_URL_ANON` / `_AUTHENTICATED` / `_SERVICE_ROLE` secrets to
  also exercise true per-role enforcement (script no-ops if absent).
- **Connection pool / pooler-URL contract**
  (`tests/test_connection_pool.py`). Three layers: (a) static grep
  asserts no module under `backend/` or `src/` (excluding
  `src/scripts/` + `tests/`) imports psycopg/asyncpg/psycopg2/pg8000
  — the backend MUST go through PostgREST over HTTPS via supabase-py;
  (b) static check that `DATABASE_URL` (when set) targets
  `*.pooler.supabase.com` not the direct `db.<ref>.supabase.co` host;
  (c) dynamic test opens `POOL_TEST_CONCURRENCY=20` concurrent
  connections and asserts every one succeeds (pooler queues, doesn't
  error). Lives in the same `concurrency-tests` ci.yml job as the
  other live-DB pytest file. Backend "503-not-500 on pool exhaustion"
  is intentionally out of scope here — that's an integration test
  belonging in Playwright E2E with a forced-exhaustion fixture; the
  test file documents this in module-level docstring.
- **DB bloat report** (`src/scripts/check_db_bloat.py`). Runs in
  `security.yml` on push + daily cron; the operator reviews weekly.
  Fails CI when any non-empty core table has `n_dead_tup /
  GREATEST(n_live_tup, 1) > 0.20` (autovacuum is throttled). Also
  prints table sizes sorted largest first as the archival hit list.
  `pgstattuple` extension auto-detected; not installed on the
  current project, so the index-bloat metric is omitted with a note
  in the report header. To enable index bloat, run `CREATE EXTENSION
  pgstattuple;` in Studio (Pro plan).
- **Slow query report** (`src/scripts/slow_query_report.py`). Read-only
  on `pg_stat_statements` v1.11 (already enabled). Three sections:
  top-10 by `total_exec_time`, anything with `mean_exec_time > 1s`,
  and hot queries (`calls >= 100`) with cache hit ratio < 99%. Fails
  CI on any finding so the operator notices; the fix is usually a
  follow-up index PR. Runs in `security.yml` on push + daily cron.
- **Grants matrix audit** (`src/scripts/check_grants_matrix.py`).
  Beyond the deny-all RLS gate in `schema_drift_check.py`, this
  enumerates every `information_schema.table_privileges` row and
  asserts: `anon` / `authenticated` / `PUBLIC` have ZERO grants on
  the 4 core tables; `service_role` + `postgres` carry the full set
  (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `REFERENCES`, `TRIGGER`,
  `TRUNCATE`); no other role appears in the matrix. Also enumerates
  `pg_roles` against `EXPECTED_ROLES` (Supabase platform roles +
  pg_* built-ins) — anything else flags a Studio CREATE ROLE or
  extension surprise. Runs in `security.yml`.
- **Function safety audit** (`src/scripts/check_function_safety.py`).
  Three checks against `pg_proc` / `role_routine_grants`:
  (a) only `EXPECTED_FUNCTIONS = {add_lead_column,
  rls_auto_enable, update_updated_at_column}` exist in `public`;
  (b) every `SECURITY DEFINER` function is owned by `postgres` and
  has `search_path` in `proconfig`;
  (c) no anon/authenticated/PUBLIC EXECUTE grant exists unless
  declared in `EXEC_GRANT_ALLOWLIST` (currently empty). Runs in
  `security.yml`.
- **Deep backup PITR verification**
  (`.github/workflows/backup-verify-deep.yml`). DISABLED by default
  (`workflow_dispatch` only). When enabled (Pro plan + Supabase
  PAT secret), runs monthly: creates a Supabase branch restored to
  `now() - 1h`, runs schema-drift + referential-integrity + row-count
  diff against the restore, deletes the branch in `if: always()`
  cleanup. Records RTO end-to-end as evidence that PITR works.
  Workflow header documents the full prerequisite list.
- **ANALYZE freshness gate** (`src/scripts/check_analyze_freshness.py`).
  Reads `pg_stat_user_tables.{last_analyze,last_autoanalyze}` for the
  4 core tables. Fails CI when any table with > `ROW_THRESHOLD=10_000`
  rows has both timestamps NULL or both older than
  `STALE_AFTER_DAYS=7`. Below threshold = report-only (small tables
  ride autovacuum just fine). For bulk-write paths (CSV upload of
  >1000 rows) the backend should call `ANALYZE leads` immediately
  after the upload completes — wire into `backend/main.py`'s
  `/upload` handler if/when volume grows.
- **JSONB GIN / expression-index suggestions**
  (`src/scripts/suggest_jsonb_indexes.py`). Advisory only — always
  exits 0. Scans `pg_stat_statements` for `@>` / `?` / `?|` / `?&`
  predicates on `leads.audit_results` + `orchestration_jobs.filters`
  and for `column->>'key'` extraction patterns; suggests
  `CREATE INDEX ... USING gin (column)` or
  `CREATE INDEX ... ((column->>'key'))` accordingly. The operator
  reads the weekly run log; a suggestion appearing multiple weeks
  is the signal to actually create the index.
- **Soft-delete decision (deliberately not adopted).** Hard delete
  is intentional for this single-operator project:
  - The pipeline already has explicit DELETE points (`/leads/clear`
    behind the X-Admin-Token gate; `/campaigns/{id}` cascades via
    the FK on `campaign_messages`). No "oops, recover the row"
    pattern needed at the single-operator scale.
  - Adopting soft delete would require every `SELECT` site to add
    `WHERE deleted_at IS NULL`, every FK constraint to respect the
    soft-delete chain, and partial indexes scoped on
    `deleted_at IS NULL`. Audit-grep would need to enforce the
    filter at every read site.
  - Recovery uses the Supabase PITR snapshot (verified by
    `backup-verify-deep.yml`) rather than tombstone rows.
  If this changes (multi-operator, audit requirement, regulatory
  retention), the soft-delete adoption checklist lives in the
  workflow comments above.
- **Migration safety preview-branch gate**
  (`.github/workflows/migration-safety.yml`). DISABLED by default
  (`workflow_dispatch` only). When enabled on PRs touching
  `supabase_schema.sql` or `supabase/migrations/**`: creates a
  preview Supabase branch, applies the new schema to it, runs
  `schema_drift_check` + `check_referential_integrity` +
  `check_query_plans` against the branch, then deletes the branch.
  Block-merge gate when added to the required status checks.
  Prerequisites in the workflow header.
- **Storage size + WoW growth monitor**
  (`src/scripts/storage_report.py`). Reports
  `pg_database_size(current_database())` and per-table
  `pg_total_relation_size`. Soft-warns at 70% of
  `STORAGE_QUOTA_BYTES` (default 8 GiB matching Supabase Pro base
  disk), hard-fails at 90%. Diffs against a baseline JSON persisted
  via `actions/cache@v4` keyed on workflow + month; any table
  growing >2x WoW contributes to FAIL ("stuck job inserting
  forever" signal). `audit_results` JSONB is the prime growth
  suspect once volume builds — the report flags it explicitly when
  it crosses thresholds. Runs in `security.yml` on push + daily
  cron.
- CI-only dep: `psycopg[binary]>=3.1` is installed inline by every
  Supabase-DB job, not added to `requirements.txt` (backend talks to
  Supabase over PostgREST HTTPS, not Postgres wire — no need to ship a
  driver into the runtime image).
- **Login brute-force gate** (`frontend/utils/loginThrottle.ts`). In-process
  per-IP throttle in front of `signInWithPassword`: 5 attempts / 60s.
  Bucket key derives from `TRUSTED_CLIENT_IP_HEADER` (same trusted-IP
  source as the proxy); spoofless callers fall back to a synthetic
  `unknown` bucket. `MAX_BUCKETS = 10_000` is a **hard cap** — when the
  expired-sweep frees nothing, the oldest bucket is evicted, so a unique-IP
  flood within one window can't pin memory. Counter increments on every
  attempt regardless of outcome; `clearLoginRate()` releases the bucket
  on successful credential check.
- **Proxy `BACKEND_URL` scheme assertion** (`frontend/app/api/proxy/[...path]/route.ts`).
  Render's `fromService.property: host` returns a bare hostname, so
  `_resolveBackendUrl()` prepends `https://` if no scheme is present.
  `_assertBackendSchemeAllowed()` runs at **request time inside `forward()`**
  (not at module load — that would crash `next build` against a dev backend
  on `http://127.0.0.1:8000`). In `NODE_ENV=production`, the resolved URL
  must be `https://` UNLESS the host is loopback (`127.0.0.1`, `localhost`,
  `*.localhost`) — that exempts `npm run start` smoke-tests against a
  local backend while still blocking any prod misconfiguration that would
  silently downgrade Render-network traffic to plaintext.
- **GDPR Article 20 — data export** at `GET /operator/data-export`
  (`backend/main.py`). Returns a ZIP with `leads.csv`, `campaigns.csv`,
  `messages.csv`, `audit_log.json` (orchestration_jobs wrapped with
  `{export_timestamp, operator_email, schema_version, row_counts}`).
  Single-operator semantics ([ADR-001](docs/adr/001-single-tenant-by-design.md))
  → the export is unconditional. CSV-injection guard
  (`sanitize_csv_cell`) on every cell; `csv.QUOTE_MINIMAL` keeps
  embedded CRLF inside one row. Rate-limit **1/day, peer-IP-keyed
  (`get_remote_address`, NOT XFF-honouring)** — closes a theoretical
  XFF-rotation bypass by an API-key holder hitting the backend's
  public URL directly. Locked in by `tests/test_gdpr_export.py`
  (17 tests). Operator-facing button: `frontend/app/page.tsx` Settings
  → "Download my data". Full doc: [docs/observability.md](docs/observability.md) §12
  + [docs/legal/privacy-policy.md](docs/legal/privacy-policy.md) §7.
- **GDPR Article 17 — right to erasure** at `DELETE /operator/account`.
  Three-factor gate: (1) `X-API-Key`, (2) `X-Admin-Token` (same gate
  as `/leads/clear`), (3) JSON body with Pydantic
  `Literal["DELETE MY ACCOUNT"]` confirmation (wrong value = 422
  BEFORE the destructive step). **Audit-first invariant**: a row is
  written to `account_deletions` BEFORE any DELETE runs — partial-
  failure paths still leave a trace; audit-write failure returns 503
  and **skips the destructive step entirely** (zero rows touched).
  FK dependency order: `campaign_messages` → `campaigns` →
  `orchestration_jobs` → `leads`. Sentinel-UUID predicate
  (`_NEVER_UUID = "00000000-..."`) on `delete().neq("id", ...)` —
  PostgREST requires a WHERE filter for safety. Footgun: a row whose
  `id` IS the all-zero UUID escapes the wipe (~2⁻¹²² probability with
  `gen_random_uuid()`); upgrade path is `.gte("created_at",
  "1970-01-01")`. Rate-limit **1/hour, peer-IP-keyed**. Locked in by
  `tests/test_gdpr_deletion.py` (16 tests: three-factor gate,
  audit-first, row counts, retention, rate limit).
- **`account_deletions` audit table** (`supabase_schema.sql`): one row
  per `DELETE /operator/account`. Schema: `{id, deleted_at,
  operator_email, remote_ip, row_counts JSONB, expires_at}`. RLS
  deny-all (matches the 4 core tables). Index on `expires_at`.
  **30-day retention** — purged daily by
  `src/scripts/purge_expired_audit_log.py` (wired into `security.yml`
  before the storage-monitor job). After 30 days, **no trace remains**
  ([docs/legal/privacy-policy.md](docs/legal/privacy-policy.md) §5).
  ⚠️ Day-1 follow-up: `EXPECTED_TABLES` in
  `src/scripts/schema_drift_check.py` needs `account_deletions` added
  + the RLS deny-all assertion list extended, or the schema-drift CI
  gate goes red on next push.

