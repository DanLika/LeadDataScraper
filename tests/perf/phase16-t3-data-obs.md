# Phase 16 — Task T3 — Data layer & observability sweep

- **Date:** 2026-05-23
- **Branch:** `chore/phase16-t3-2026-05-23` (cut from `origin/main`)
- **Supabase project:** `kbtkxpvchmunwjykbeht` (Lead Scraper, region `eu-west-1`, Postgres `17.6.1.084`)
- **Tool path:** Supabase MCP `execute_sql` (RW) + `list_tables` + `get_advisors` + `get_logs`. `DATABASE_URL` not provisioned in this env; `psql`/`psycopg` unavailable. MCP `execute_sql` was sufficient for T3.1–T3.11, T3.18–T3.20.
- **Test row namespace:** `_t3_test_*` (cleaned at end — zero residue verified, see T3.21).
- **Hard-stop conditions encountered:** None. RLS holds on every table; no SECURITY EMERGENCY.

---

## Pre-flight technique validations

Three MCP techniques validated **once** before being relied on:

| Technique                                              | Result | Note |
|--------------------------------------------------------|--------|------|
| `account_deletions` table exists                       | ✅ yes | `to_regclass('public.account_deletions')` returned the relation. (Day-1 follow-up listed in CLAUDE.md — already shipped to the live DB.) |
| `set_config('role','anon',true)` switches RLS context  | ✅ yes | `SELECT count(*) FROM public.leads` after `set_config('role','anon',true)` errored `42501 permission denied`. Both `REVOKE` (grant-layer deny) and RLS deny-all hold — defense-in-depth confirmed. |
| `SET LOCAL statement_timeout` fires real cancellation  | ✅ yes | `SET LOCAL statement_timeout='1500ms'; SELECT pg_sleep(3)` → `57014 canceling statement due to statement timeout`. MCP surfaces the cancellation; not masked by MCP-side timeout. |

---

## Table inventory snapshot

| Table                      | RLS  | Live rows | Total size | Dead tuples | Last autovacuum             |
|----------------------------|------|-----------|------------|-------------|-----------------------------|
| `public.leads`             | ✅   | 21        | 240 KB     | 0           | 2026-05-23 08:48:50 UTC     |
| `public.campaign_messages` | ✅   | 0         |  96 KB     | 14          | 2026-05-18 11:02:27 UTC     |
| `public.orchestration_jobs`| ✅   | 0         |  64 KB     | 49          | 2026-05-21 18:26:05 UTC     |
| `public.campaigns`         | ✅   | 0         |  48 KB     | 14          | **NULL** (never autovacuumed)|
| `public.account_deletions` | ✅   | 0         |  24 KB     | 0           | NULL (empty table)          |

Database total: **12 MB** — far from the Supabase Pro 8 GiB plan ceiling (`pg_database_size(current_database())`).

---

## T3.1 — Live query plans (EXPLAIN ANALYZE, `enable_seqscan = off`)

Per Faza 5.3, `enable_seqscan=off` is used so the planner picks any *usable* index on the empty / nearly-empty tables. If it falls back to `Seq Scan` with that disabled, the index doesn't exist.

| # | Query                                                            | Plan node                                   | Index                                     | Exec ms |
|---|------------------------------------------------------------------|---------------------------------------------|-------------------------------------------|---------|
| 1 | `SELECT * FROM leads ORDER BY created_at DESC LIMIT 200`         | `Limit` → `Index Scan`                      | `idx_leads_created_at_desc`               | 1.75    |
| 2 | `SELECT * FROM leads WHERE audit_status='Pending'`               | `Index Scan`                                | `idx_leads_audit_status`                  | 1.31    |
| 3 | `SELECT * FROM leads WHERE unique_key='_t3_test_probe_001'`      | `Index Scan`                                | `leads_pkey`                              | 2.41    |
| 4 | `SELECT * FROM campaign_messages WHERE campaign_id=…`            | `Index Scan`                                | `idx_campaign_messages_campaign_id`       | 0.94    |
| 5 | `SELECT * FROM leads WHERE seo_score BETWEEN 50 AND 100`         | **`Seq Scan`** (planner penalty cost 1e10)  | **none**                                  | 0.79    |
| 6 | `SELECT * FROM orchestration_jobs WHERE status='running'`        | `Index Scan`                                | `idx_orchestration_jobs_status`           | 1.23    |
| 7 | `SELECT * FROM account_deletions WHERE expires_at < now()`       | `Index Scan`                                | `idx_account_deletions_expires_at`        | 0.06    |

**FINDING T3.1-A (MEDIUM) — no index on `leads.seo_score`.** Query #5 falls through `enable_seqscan=off` because no index exists. Trivial at 21 rows; UI filter / `/insights` aggregation pain at production scale.

- **Remediation:** `CREATE INDEX idx_leads_seo_score ON public.leads (seo_score) WHERE seo_score IS NOT NULL;` (partial — `seo_score` is NULL for every current row).
- **Gate gap:** `src/scripts/check_query_plans.py::HOT_PATH_QUERIES` covers four queries (created_at, audit_status, unique_key, campaign_messages.campaign_id) and would not have detected this. Add the score-range query to the list.

---

## T3.2 — JSONB shape conformance on real data

```sql
SELECT … FROM public.leads WHERE audit_status='Completed' LIMIT 50;
```

**Result:** `[]` — zero `Completed` rows. The 21 live leads are post-clear / pre-audit (all `audit_results IS NULL`).

**Conclusion:** Shape conformance against live data is **deferred until pipeline runs**. The contract is still enforced by `src/scripts/check_jsonb_shapes.py` (push + daily cron), so any future producer regression that lands non-conforming JSONB will trip the gate.

When live data exists, re-run:

```sql
SELECT unique_key, audit_results ?& ARRAY['score','is_up','tech_flags','red_flags'] AS has_all,
       jsonb_typeof(audit_results->'score') AS score_type,
       jsonb_typeof(audit_results->'is_up') AS is_up_type,
       jsonb_typeof(audit_results->'tech_flags') AS tech_flags_type,
       jsonb_typeof(audit_results->'red_flags') AS red_flags_type
FROM public.leads WHERE audit_status='Completed';
```

---

## T3.3 — NULL audit on real data (21 leads)

| Column                | NULL count | Notes                                                                 |
|-----------------------|-----------:|-----------------------------------------------------------------------|
| `name`                | 1          | Schema allows NULL; app reads as required → TIGHTEN candidate.        |
| `company_name`        | 1          | Same row as `name`-null (likely a partial Maps scrape).               |
| `website`             | 1          | Same partial-row pattern.                                             |
| `email`               | 21 (100%)  | Maps doesn't surface contact emails — pipeline-expected.              |
| `phone`               | 21 (100%)  | Same.                                                                 |
| `audit_status`        | 0          | NOT NULL invariant holds.                                             |
| `audit_results`       | 21 (100%)  | No `Completed` rows yet (see T3.2). Not a finding.                    |
| `seo_score`           | 21 (100%)  | Audit hasn't run on these rows.                                       |
| `outreach_score`      | 21 (100%)  | Same.                                                                 |
| `segment`             | 21 (100%)  | Same — regex segmenter runs at audit time.                            |
| `lead_source`         | 1          | App reads as required → TIGHTEN candidate.                            |
| `created_at`/`updated_at` | 0      | Default `now()` invariant holds.                                      |

**FINDING T3.3-A (INFO) — three "TIGHTEN candidate" columns** (`name`, `lead_source`, possibly `company_name`) are app-required but schema-nullable. Documented in CLAUDE.md null-ratio audit. Promote to NOT NULL only when the producer side guarantees the value — premature NOT NULL on the scraper output path will fail valid partial rows.

---

## T3.4 — Orphans + zombies + stuck leads sweep

Single combined query covering all checks in `src/scripts/check_orphans_and_zombies.py`:

| Check                                                  | Count |
|--------------------------------------------------------|------:|
| `campaign_messages` → `leads` orphan (lead_unique_key) |   0   |
| `campaign_messages` → `campaigns` orphan (campaign_id) |   0   |
| Zombie `orchestration_jobs` running > 4h               |   0   |
| Stuck leads (Pending/Processing > 24h)                 |   0   |
| `campaign_messages.sent_at IS NOT NULL AND status='pending'` |   0   |
| `audit_status='Completed' AND audit_results IS NULL`   |   0   |

**All invariants hold.** No auto-heal triggered.

---

## T3.5 — CHECK constraint enforcement (live INSERT trials)

Each trial tried to insert a row violating one constraint; all seven failed with `23514 check_violation` (NOT 500). Cleanup verified — zero `_t3_test_*` rows remain (the INSERTs never landed).

| # | INSERT payload                                          | Constraint hit                          | Result |
|---|---------------------------------------------------------|-----------------------------------------|--------|
| 1 | `leads(audit_status='InvalidValue')`                    | `leads_audit_status_allowed`            | ✅ 23514 |
| 2 | `leads(seo_score=200)`                                  | `leads_seo_score_range`                 | ✅ 23514 |
| 3 | `leads(outreach_score=150)`                             | `leads_outreach_score_range`            | ✅ 23514 |
| 4 | `leads(email='ab')` (length < 3)                        | `leads_email_basic_shape`               | ✅ 23514 |
| 5 | `leads(enrichment_status='invalid_state')`              | `leads_enrichment_status_allowed`       | ✅ 23514 |
| 6 | `orchestration_jobs(status='bogus_status')`             | `orchestration_jobs_status_allowed`     | ✅ 23514 |
| 7 | `campaigns(channel='fax')`                              | `campaigns_channel_allowed`             | ✅ 23514 |

The Supabase postgres log confirms each rejection (T3.21 sanity).

Full constraint inventory (verified via `pg_constraint c.contype='c'`): 10 CHECKs — matches CLAUDE.md `EXPECTED_CHECK_CONSTRAINTS`.

---

## T3.6 — statement_timeout per role (`pg_db_role_setting`)

```sql
SELECT r.rolname, setconfig FROM pg_db_role_setting s
  JOIN pg_roles r ON r.oid = s.setrole
  WHERE r.rolname IN ('anon','authenticated','service_role','authenticator','postgres');
```

| Role             | `setconfig` entry                                                            |
|------------------|------------------------------------------------------------------------------|
| `anon`           | `statement_timeout=3s` ✅                                                    |
| `authenticated`  | `statement_timeout=8s` ✅                                                    |
| `authenticator`  | `session_preload_libraries=safeupdate, statement_timeout=8s, lock_timeout=8s` ✅ (the PostgREST connection role) |
| `service_role`   | `statement_timeout=30s` ✅ (set by `set_service_role_statement_timeout` migration) |
| `postgres`       | `search_path="$user", public, extensions` (no timeout — superuser bypasses)  |

The pre-flight `pg_sleep(3)` under `SET LOCAL statement_timeout='1500ms'` already proved the cancellation primitive fires. Both layers pass.

---

## T3.7 — RLS verification (anon / authenticated SELECT + INSERT + RPC)

Each query: `SELECT set_config('role','anon',true); <op>`. Combined into one execute_sql so the role switch and the operation share a transaction.

| Op                                                | Result                                                            |
|---------------------------------------------------|-------------------------------------------------------------------|
| anon SELECT `leads`                               | ✅ `42501 permission denied for table leads`                      |
| anon SELECT `campaigns`                           | ✅ `42501 permission denied for table campaigns`                  |
| anon SELECT `campaign_messages`                   | ✅ `42501 permission denied for table campaign_messages`          |
| anon SELECT `orchestration_jobs`                  | ✅ `42501 permission denied for table orchestration_jobs`         |
| anon SELECT `account_deletions`                   | ✅ returns `effective_role=anon, visible=0` — RLS restrictive `account_deletions_deny_all` filters all rows (the GRANT is wide; see T3.8-A) |
| anon INSERT into `leads`                          | ✅ `42501 permission denied for table leads` (no GRANT INSERT)    |
| anon RPC `add_lead_column('…')`                   | ✅ `42501 permission denied for function add_lead_column`         |
| anon RPC `exec_sql('SELECT 1')`                   | ✅ `42883 function public.exec_sql(unknown) does not exist` — regression guard intact |
| authenticated SELECT `leads`                      | ✅ `42501 permission denied for table leads`                      |
| postgres RPC `add_lead_column('drop table leads; --')` | ✅ `P0001 invalid column name` — regex allowlist blocks SQL-injection-shaped names |

`add_lead_column` body re-verified (`pg_get_functiondef`): `SECURITY DEFINER`, `SET search_path TO 'pg_catalog','public'`, regex `'^[A-Za-z_][A-Za-z0-9_]{0,62}$'`, no string interpolation outside `format(... %I ...)`.

**No SECURITY EMERGENCY** — RLS + grants + RPC allowlist + RPC arg validation all hold.

---

## T3.8 — Grants matrix

```sql
SELECT grantee, table_name, string_agg(privilege_type, ',') FROM information_schema.table_privileges
  WHERE table_schema='public' AND table_name IN (5 core tables) GROUP BY grantee, table_name;
```

Compact:

| Table                  | Grantees with privileges                                      |
|------------------------|---------------------------------------------------------------|
| `leads`                | `postgres`, `service_role`                                    |
| `campaigns`            | `postgres`, `service_role`                                    |
| `campaign_messages`    | `postgres`, `service_role`                                    |
| `orchestration_jobs`   | `postgres`, `service_role`                                    |
| **`account_deletions`**| **`postgres`, `service_role`, `anon`, `authenticated`** ⚠️    |

Each non-anon/non-authenticated grantee has the full `DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE` set. No `PUBLIC` row anywhere.

**FINDING T3.8-A (HIGH) — `account_deletions` grants are leaked to `anon` and `authenticated`.** The CLAUDE.md spec for these tables is "No PUBLIC grants" and `anon`/`authenticated` = 0 grants on the 4 (now 5) core tables. The RLS `account_deletions_deny_all` policy (restrictive, `using=false`) is the only thing blocking access today.

- **Severity:** HIGH on principle (defense-in-depth break), MEDIUM in practice (RLS catches it). Risk surface: any future regression that disables RLS, drops the deny-all policy, or introduces a permissive policy that overrides the restrictive one would immediately expose the audit-log table to `anon`.
- **Remediation:**
  ```sql
  REVOKE ALL ON public.account_deletions FROM anon, authenticated, PUBLIC;
  ```
  Apply via a Supabase migration so it survives a future schema rebuild.
- **Gate gap:** `src/scripts/check_grants_matrix.py` and `src/scripts/schema_drift_check.py::EXPECTED_TABLES` need `account_deletions` added to the assertion list (already a flagged Day-1 follow-up in CLAUDE.md — this finding is the live confirmation).

**FINDING T3.8-B (LOW) — policy permissivity mismatch.** The other four deny-all policies (`leads_deny_all`, `campaigns_deny_all`, `campaign_messages_deny_all`, `orchestration_jobs_deny_all`) are PERMISSIVE (`polpermissive=true`); `account_deletions_deny_all` alone is RESTRICTIVE (`polpermissive=false`). Both deny when `using_expr='false'`, but RESTRICTIVE is strictly harder to bypass (a future permissive policy can't override it). Either standardize on RESTRICTIVE for consistency *and* extra safety, or document the rationale for the mismatch in CLAUDE.md.

---

## T3.9 — Function safety audit

Three functions in `public` (matches CLAUDE.md `EXPECTED_FUNCTIONS = {add_lead_column, rls_auto_enable, update_updated_at_column}`). **`exec_sql` is NOT present** — regression test intact.

| Function                  | SECDEF | Owner    | `search_path`                | EXEC granted to                                            |
|---------------------------|--------|----------|------------------------------|------------------------------------------------------------|
| `add_lead_column(text)`   | ✅     | postgres | `pg_catalog, public`         | `postgres`, `service_role` only ✅                         |
| `rls_auto_enable()`       | ✅     | postgres | `pg_catalog`                 | `postgres`, `service_role` only ✅                         |
| `update_updated_at_column()` | ❌  | postgres | `pg_catalog, public`         | `postgres`, `service_role`, **`anon`, `authenticated`, `PUBLIC`** ⚠️ |

**FINDING T3.9-A (LOW) — `update_updated_at_column` is over-granted.** Spec: "no anon/authenticated/PUBLIC EXECUTE grant exists unless declared in `EXEC_GRANT_ALLOWLIST`". Two paths:

- **Option A (recommended):** add `update_updated_at_column` to `EXEC_GRANT_ALLOWLIST` in `src/scripts/check_function_safety.py`. It's a trigger function (not SECDEF, not exposed as a PostgREST RPC, returns a `NEW` record useless outside trigger context) — practically not exploitable.
- **Option B:** `REVOKE EXECUTE ON FUNCTION public.update_updated_at_column() FROM PUBLIC, anon, authenticated;` Triggers run with the user's privileges only by side effect; the trigger machinery itself doesn't need EXECUTE granted to anon/authenticated for the function to fire (the trigger fires under the role that owns the table, modulated by RLS).

---

## T3.10 — Dead-tuple ratio + table sizes

(See "Table inventory snapshot" above for full row.) All `dead_pct` are 0.00 — no bloat.

| Notable point                                                          | Notes |
|------------------------------------------------------------------------|-------|
| `campaigns.last_autovacuum = NULL`                                     | Has 14 dead tuples on 0 live rows; never autovacuumed. Threshold not yet crossed. Manual `VACUUM public.campaigns;` if dead count grows. Not currently a finding. |
| `last_analyze = NULL` on every table                                   | Only `last_autoanalyze` populated. Autovacuum's analyze daemon does the work; manual `ANALYZE` not needed unless statistics drift. |
| 14 / 49 / 14 dead tuples on `campaign_messages` / `orchestration_jobs` / `campaigns` with 0 live | Recent test rows; autovacuum will clean asynchronously. Not a finding. |

`pg_total_relation_size` per table is dominated by indexes for `leads` (21 rows, 240 KB) — typical pattern.

---

## T3.11 — `pg_stat_statements` top 20 by total_exec_time

Extension status: `pg_stat_statements 1.11` ✅ (`pgstattuple` not installed; documented in CLAUDE.md).

Top 20 (excluding `EXPLAIN%` and self):

| Calls   | Mean (ms) | Cache hit % | Sample                                                                  |
|--------:|----------:|------------:|-------------------------------------------------------------------------|
| 3107    | 16.19     | 100.00      | `SELECT * FROM leads ORDER BY created_at DESC LIMIT/OFFSET` (`/leads`)  |
| 19      | 660.76    | (Studio)    | `SELECT name FROM pg_timezone_names` — Supabase Studio metadata call    |
| 195     | 31.74     | 99.99       | `INSERT INTO leads (...) WITH pgrst_source AS …`                        |
| 11      | 267.13    | 100.00      | `SELECT … FROM pg_available_extensions` — Studio                        |
| 19213   | 0.14      | 100.00      | `SELECT users.aud, … FROM auth.users` — Supabase Auth                   |
| 8       | 333.02    | (no read)   | `pg_backup_start(...)` — PITR base-backup harness                       |
| 65      | 39.18     | 100.00      | `DELETE FROM auth.users WHERE email = $1` — test-user teardown          |
| 48      | 46.10     | 100.00      | `INSERT INTO auth.users (...)` — test-user provisioning                 |
| 194     | 11.38     | 100.00      | `SELECT FROM leads WHERE audit_status <> $1 OR enrichment_status <> $2 AND retry_count < $3 ORDER BY …` — `/process-all` candidates filter |
| 5758    | 0.37      | 100.00      | `set_config('search_path',…), set_config('role',…), …` — PostgREST per-request session bootstrap |
| 68      | 28.75     | 99.96       | `SELECT audit_status, audit_results, seo_score, lead_source FROM leads LIMIT/OFFSET` — `/insights` strategic-insights call (matches CLAUDE.md pinned finding "SELECTs only 5 fields") |
| 203     | 7.56      | 100.00      | `UPDATE orchestration_jobs SET processed_count = …` — orchestrator tick |
| 17004   | 0.07      | 100.00      | `SELECT … FROM auth.sessions` — Supabase Auth                           |
| 7       | 160.94    | 100.00      | `WITH tables AS (SELECT c.oid … FROM pg_class c) …` — Studio table list |
| 12      | 90.19     | 100.00      | `DO $$ … 'claude-audit-test@example.com' …` — earlier ultrareview run    |
| 434     | 2.12      | 100.00      | `SELECT name, company_name, audit_status, seo_score, lead_source FROM leads LIMIT/OFFSET` — `/leads` thin projection variant |

**No query has `mean_exec_time > 1s`.** Highest application-path mean is `INSERT INTO leads` at 31.7 ms; highest read-path application mean is `/leads LIMIT/OFFSET` at 16.2 ms. Cache hit ratio on every hot path is **≥99.93 %**; only Studio-side `pg_timezone_names` shows the read-blocks-stat-absent pattern (small table, planner choice).

The CLAUDE.md "slow query report fails CI on any finding" threshold (`mean > 1 s`) would not trip on current state. Re-run after first 1k-lead audit pipeline run.

---

## T3.12 — Cooperative cancel (static; live deferred)

**Spec verified in `src/core/parallel_auditor.py`:**

- `self.status["stop_requested"]` boolean flag (line 33).
- `stop()` method (line 36–37) sets it.
- `_raise_if_stop_requested()` (line 40–50) raises `asyncio.CancelledError("stop requested by operator")` when true.
- Called at 9 cooperative-cancel points (lines 128, 132, 139, 192, 196, 206, 215, 295, plus per-lead `if self.status.get("stop_requested"):` at line 295).
- `except asyncio.CancelledError` blocks (lines 155, 254) log `"Hunt cancelled by stop request"` / `"Audit cancelled by stop request"` and **re-raise** so `asyncio.gather()` sees a cancellation, not a per-task failure.

**Faza 9 invariant** ("Either untouched Pending OR fully Completed OR Failed-with-last_error; no torn writes") is enforced at the writer side: cancelled leads do not flow through the `Failed`-marking branch — the re-raise bypasses it. Untouched-row count is whatever the chunk hadn't reached.

**Live deferred — repro for operator:**

```bash
# Backend must be running.
curl -X POST $BACKEND_URL/orchestrator/start \
     -H "X-API-Key: $API_SECRET_KEY" -H "Content-Type: application/json" \
     -d '{"task":"AUDIT","filters":{"type":"us_leads"}}'
# Wait ~2 s.
curl -X POST $BACKEND_URL/audit/stop -H "X-API-Key: $API_SECRET_KEY"
# Verify per-row state:
SELECT audit_status, count(*) FROM leads GROUP BY audit_status;
# Each row should be Pending (untouched), Completed (fully done), or Failed (with non-null last_error).
SELECT count(*) FROM leads WHERE audit_status='Failed' AND last_error IS NULL; -- expect 0
SELECT count(*) FROM leads WHERE audit_status='Completed' AND audit_results IS NULL; -- expect 0
```

---

## T3.13 — `/stats` cache stampede protection (static; live deferred)

**Spec verified in `src/utils/stats_cache.py`:**

- `_StatsCache.__init__` constructs `self._lock = asyncio.Lock()`.
- `get(build_fn)` does **double-checked locking**: fast path `if self._fresh(): return self._payload`; slow path `async with self._lock: if self._fresh(): return self._payload; payload = await build_fn(); …`.
- `invalidate()` is lock-free (`self._expires_at = 0` semantically; "Cheap and lock-free — worst case is one in-flight request reads the about-to-be-flushed value, which is acceptable for a cache").
- Module-level singleton, per-uvicorn-worker process. No cross-loop sharing.

**Live deferred — repro:**

```bash
# Restart backend so cache is cold.
# Fire 50 concurrent /stats:
for i in $(seq 1 50); do
  (curl -s $BACKEND_URL/stats -H "X-API-Key: $API_SECRET_KEY" >/dev/null) &
done; wait
# Tail backend log for "rebuild" — must appear exactly 1×; the other 49 should hit the cached payload.
```

---

## T3.14 — Browser pool lifecycle (static; live deferred)

**Spec verified in `src/scrapers/enrichment_engine.py`:**

- `self._pw: Optional[Playwright]` + `self._browser: Optional[Browser]` (lines 62–63).
- `self._browser_lock = asyncio.Lock()` (line 64).
- `_get_browser()` (line 67–83): fast path returns existing browser; slow path locks, double-checks, then `await async_playwright().start()` + `chromium.launch(headless=True)` exactly once.
- `aclose()` (line 85+): closes browser then stops playwright, sets both to None.
- Per-lead `browser.new_context(...)` for isolation (lines 128, 251).

**Caller invariant verified in `src/core/task_orchestrator.py`:**

- `_process_in_chunks` (line 305) has `finally:` block which calls `await enricher.aclose()` (line 314) **before** `stats_cache.invalidate()` (line 321).
- `_execute_deep_enrichment` similarly closes its engine in a `finally`.

**No new direct callers introduced in T3 trial code.** Static-grep for `EnrichmentEngine(`:

- `backend/main.py` (constructed inside orchestrator path only)
- `src/core/task_orchestrator.py` (the canonical caller; teardown verified)

**Live deferred — repro:**

```bash
# Before: count chromium / playwright processes
ps -ef | grep -E 'chromium|playwright' | grep -v grep | wc -l
# Trigger a discovery + enrichment job (1 lead is enough to observe launch/teardown):
curl -X POST $BACKEND_URL/orchestrator/start -H "X-API-Key: $API_SECRET_KEY" \
     -H "Content-Type: application/json" \
     -d '{"task":"DEEP_ENRICH","filters":{"type":"us_leads"}}'
# Watch backend log for one "chromium.launch" call; after job completes, count should return to baseline.
ps -ef | grep -E 'chromium|playwright' | grep -v grep | wc -l
```

---

## T3.15 — Sentry delivery (static; live deferred)

**Backend (`backend/main.py:94–131`)** ✅:

- `load_dotenv()` then `_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()`.
- `if _SENTRY_DSN:` gates the entire init — dev with no DSN stays clean.
- `_scrub_sensitive(event, hint)` defined as `before_send` callback. (`# pragma: no cover — Sentry-only path` annotation.)
- `sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0.1, send_default_pii=False, before_send=_scrub_sensitive)`.
- Per-request middleware (line 569+) tags `sentry_sdk.set_tag("request_id", rid)` and `sentry_sdk.set_user({"email": operator_email})` inside the per-request scope.

**Frontend** ✅ (canonical `@sentry/nextjs@10` layout):

```
frontend/instrumentation.ts          (Next.js server hook)
frontend/instrumentation-client.ts   (browser; reads NEXT_PUBLIC_SENTRY_DSN)
frontend/sentry.server.config.ts     (Node runtime; tracesSampleRate: 0.1)
frontend/sentry.edge.config.ts       (Edge runtime; tracesSampleRate: 0.1)
frontend/next.config.ts              (withSentryConfig + tunnelRoute: "/monitoring")
```

**Live deferred — repro** (needs `SENTRY_TEST_ENABLED=1` set on the backend):

```bash
curl -X POST $BACKEND_URL/_sentry/test -H "X-API-Key: $API_SECRET_KEY"
# After ~60 s, query Sentry API for events with tag:request_id:<rid-from-response-header>
# Frontend equivalent: open dashboard, throw an error in DevTools console.
# Cross-tier check: request_id tag on the captured event must match between frontend + backend pair.
```

---

## T3.16 — Web Vitals beacon ingestion (static; live deferred)

**Backend (`backend/main.py:699–718`)** ✅:

- `class WebVitalsMetric(BaseModel)`: `name: Literal[...]`, bounded numeric `value`, Literal `rating`, bounded `id` / `path`.
- `@app.post("/metrics", dependencies=[Depends(verify_api_key)])` `async def submit_web_vitals(request: Request, metric: WebVitalsMetric)`.
- `slowapi` 60/min limiter on the endpoint.

**Frontend (`frontend/app/components/WebVitalsReporter.tsx`)** ✅:

- Dynamic import of `web-vitals` keeps the ~3 KB lib off the initial bundle.
- Registers `onCLS`, `onINP`, `onLCP`, `onFCP`, `onTTFB`.
- `sendBeaconPayload()` uses `navigator.sendBeacon(url, new Blob([json], {type: 'application/json'}))` — bare `sendBeacon` defaults to `text/plain` and would 422 against Pydantic.
- Fallback: `fetch(url, { method:'POST', keepalive: true, … })` for browsers without `sendBeacon`.

**Live deferred — repro** (needs live frontend traffic):

```bash
# Tail backend log; "/metrics" entries should land with name in {CLS, INP, LCP, FCP, TTFB}.
# Aggregate p50/p75/p95 once volume builds.
# Per CLAUDE.md POOR thresholds: LCP>4s, INP>500ms, CLS>0.25 should each generate WARN log lines.
```

---

## T3.17 — Structured logging fields (static + live sample)

**Spec verified in `src/utils/logging_config.py`** ✅:

- ContextVars: `request_id_var`, `user_id_var`, `route_var` (lines 54–56).
- `JsonFormatter.format()` (line 69+) builds the envelope with reserved keys `timestamp`, `level`, `logger`, `message`, `request_id`, `user_id`, `route`, optional `duration_ms`, `exception`.
- Merges any `extra={...}` keys at the **top level** (line 92+), each value pre-validated via `json.dumps(value)` to ensure serializability before write.
- Final write: `json.dumps(envelope, default=str, ensure_ascii=False)`.
- `_CRLFScrubFilter` (line 108+) scrubs `record.msg`, `record.args` (tuple + dict forms), and every non-reserved `extra={...}` key in `record.__dict__` (line 145).
- Wired in `setup_logging`: `formatter = JsonFormatter(); scrub = _CRLFScrubFilter()` (lines 175–176), attached to both stdout + rotating file handlers.
- `_request_context_middleware` tokens: `tok_r = request_id_var.set(request_id); tok_u = user_id_var.set(user_id); tok_p = route_var.set(route)` (lines 230–232); reset in finally (lines 239–241).

**Live sample of backend stdout logs not accessible via Supabase MCP** — those land in Render's stdout aggregation. The Supabase MCP `get_logs(service='postgres')` returned 102 lines of Postgres-side events; format is Supabase's Logflare wrapper `{event_message, error_severity, timestamp, id, identifier}` (not the LDS `JsonFormatter` envelope). Among them, the T3.5 + T3.7 trials landed exactly as expected:

```
ERROR  new row for relation "leads" violates check constraint "leads_audit_status_allowed"
ERROR  new row for relation "leads" violates check constraint "leads_seo_score_range"
ERROR  new row for relation "leads" violates check constraint "leads_outreach_score_range"
ERROR  new row for relation "leads" violates check constraint "leads_email_basic_shape"
ERROR  new row for relation "leads" violates check constraint "leads_enrichment_status_allowed"
ERROR  new row for relation "orchestration_jobs" violates check constraint "orchestration_jobs_status_allowed"
ERROR  new row for relation "campaigns" violates check constraint "campaigns_channel_allowed"
ERROR  insert or update on table "campaign_messages" violates foreign key constraint "campaign_messages_campaign_id_fkey"
ERROR  permission denied for table leads
ERROR  permission denied for table campaigns
ERROR  permission denied for table campaign_messages
ERROR  permission denied for table orchestration_jobs
ERROR  permission denied for function add_lead_column
ERROR  function public.exec_sql(unknown) does not exist
ERROR  invalid column name
```

Confirms the trials actually executed at the DB layer (positive control for T3.5 / T3.7 / T3.19).

**Live deferred — repro for backend stdout sample** (needs Render CLI or dashboard log export):

```bash
render logs -s lds-backend --num 100 --json | jq '.timestamp, .level, .request_id, .route, .duration_ms'
# Every line must parse; every HTTP handler line must carry request_id + route + duration_ms.
```

---

## T3.18 — Storage growth + DB size

- `pg_database_size('postgres')` = **12 MB**.
- Plan baseline 8 GiB (Supabase Pro) → 0.15 % utilization.
- Per-table sizes already listed in the snapshot. No table > 240 KB.

WoW growth deferred — no `.storage_baseline.json` snapshot from a prior run available in this branch. Once `security.yml::storage-monitor` runs once it persists the baseline; this report is the canonical 2026-05-23 reference for future diffs.

Projection: assuming 1 KB / lead row (typical PostgreSQL row + index width for a ~30-column row with JSONB), and 8 GiB plan ceiling → **~8 million leads** before plan upgrade required. At a realistic 1 k leads/day pipeline, that's ~22 years runway. Not a near-term concern.

---

## T3.19 — Foreign key + cascade live verification

Sequence (each statement its own MCP call, committed):

1. `INSERT INTO public.campaigns (...) VALUES ('_t3_test_cascade', 'email', 'draft') RETURNING id` → `96520cd5-e920-43ef-b645-c50541ac631a`.
2. `INSERT INTO public.campaign_messages (campaign_id, …) VALUES (×3) RETURNING id` → 3 rows committed.
3. `INSERT INTO public.campaign_messages (campaign_id='00000000-1111-2222-3333-444444444444', …)` → ✅ `23503 foreign key constraint "campaign_messages_campaign_id_fkey"` — FK rejection on bad parent confirmed.
4. `SELECT count(*) WHERE campaign_id='96520cd5-…'` → **3** (before).
5. `DELETE FROM public.campaigns WHERE id='96520cd5-…' RETURNING id` → 1 row deleted.
6. `SELECT count(*) WHERE campaign_id='96520cd5-…'` → **0** (CASCADE worked).

Cascade FK on `campaign_messages.campaign_id → campaigns.id` exercised end-to-end. No follow-on FK chain on `account_deletions` (no FK to other tables — designed as a standalone audit ledger).

---

## T3.20 — Backup + PITR readiness

WAL archival layer:

| Setting             | Value                                                                  | OK |
|---------------------|------------------------------------------------------------------------|----|
| `archive_mode`      | `on`                                                                   | ✅ |
| `archive_command`   | `/usr/bin/admin-mgr wal-push %p >> /var/log/wal-g/wal-push.log 2>&1`   | ✅ (Supabase WAL-G) |
| `wal_level`         | `logical`                                                              | ✅ |
| `max_wal_senders`   | `5`                                                                    | ✅ |
| `synchronous_commit`| `on`                                                                   | ✅ |
| `pg_stat_archiver.archived_count` | **590**                                                  | ✅ WAL flowing |
| `pg_stat_archiver.failed_count`   | **0**                                                    | ✅ |
| `pg_stat_archiver.last_archived_time` | 2026-05-23 09:19:02 UTC (seconds ago)                | ✅ live |
| `pg_replication_slots` rows       | 0                                                        | (no logical replication peer; expected) |

PITR retention window (1 day on Free / 7 days on Pro) is a Supabase-dashboard setting, not a Postgres-introspection signal — operator must verify via the Supabase dashboard. Project is `ACTIVE_HEALTHY`. Estimated restore RTO at current DB size (12 MB) is single-digit minutes.

Deep PITR drill (creating a Supabase branch, restoring to `now()-1h`, re-running schema-drift + integrity gates) is the responsibility of `.github/workflows/backup-verify-deep.yml` (currently `workflow_dispatch`-only). Run quarterly per CLAUDE.md guidance.

---

## T3.21 — Cleanup verification

Final row counts:

| Table                  | Live rows | T3-test residue |
|------------------------|----------:|----------------:|
| `leads`                | 21        | 0               |
| `campaigns`            | 0         | 0               |
| `campaign_messages`    | 0         | 0               |
| `orchestration_jobs`   | 0         | 0               |
| `account_deletions`    | 0         | n/a             |

Cleanup notes:

- T3.5 (CHECK constraint trials): every INSERT failed at constraint check — no rows ever committed. No cleanup needed.
- T3.19 (cascade): the `_t3_test_cascade` campaign was deleted as the test step; cascade removed its 3 messages. No residue.
- T3.7 anon trials: each failed with `permission denied` before any write happened.

`DELETE … WHERE unique_key LIKE '\_t3\_test\_%' ESCAPE '\'` on both `leads` and `campaigns` returned `[]` (no rows to delete) — the LIKE escape syntax is intentional (`_` is a wildcard in LIKE; `\_` is a literal underscore via `ESCAPE '\'`).

---

## Findings summary

| ID            | Severity | Title                                                       | Remediation                                                  |
|---------------|----------|-------------------------------------------------------------|--------------------------------------------------------------|
| **T3.1-A**    | MEDIUM   | No index on `leads.seo_score`                               | Add partial index + extend `check_query_plans.py::HOT_PATH_QUERIES` |
| **T3.3-A**    | INFO     | `leads.name`, `lead_source` schema-nullable but app-required | Tighten only after producer guarantees value                |
| **T3.8-A**    | HIGH     | `account_deletions` GRANTed to anon + authenticated         | `REVOKE ALL ON public.account_deletions FROM anon, authenticated, PUBLIC;` (migration) + extend `EXPECTED_TABLES` in `schema_drift_check.py` + `check_grants_matrix.py` |
| **T3.8-B**    | LOW      | `account_deletions_deny_all` is RESTRICTIVE while peers PERMISSIVE | Standardize on RESTRICTIVE for all five tables (stronger) or document the mismatch |
| **T3.9-A**    | LOW      | `update_updated_at_column` EXECUTE granted to PUBLIC/anon/authenticated | Add to `EXEC_GRANT_ALLOWLIST` (trigger function, not exploitable) OR REVOKE EXECUTE |
| Advisor INFO  | INFO     | 2 unused indexes (`idx_campaigns_status`, `idx_campaign_messages_lead_unique_key`) | Keep until campaigns actively used; re-evaluate at 1 k campaigns |
| Advisor WARN  | INFO     | Leaked-password protection disabled (Supabase Auth)         | Single-operator deployment, deferred until multi-tenant     |

Items locked-in / no finding: T3.4 (zero orphans/zombies/state-violations), T3.5 (7/7 CHECK constraints fire), T3.6 (per-role timeouts exact match), T3.7 (RLS + grants + RPC allowlist all deny anon), T3.11 (no hot query > 1 s mean, cache hit ≥99.93 %), T3.12–T3.17 (static contract intact), T3.19 (FK cascade verified), T3.20 (WAL archival live, 0 failures).

---

## Reproducing this report

The 21 sub-tasks were executed in the order T3.1 → T3.21. Every SQL statement is inline above. To re-run from scratch:

1. Cut `chore/phase16-t3-YYYY-MM-DD` from `origin/main`.
2. Confirm Supabase MCP can reach project `kbtkxpvchmunwjykbeht`.
3. Validate the three pre-flight techniques (account_deletions exists, role-switch fires, statement_timeout cancels).
4. Run each task's SQL block via MCP `execute_sql`. Live-deferred tasks (T3.12–T3.16) require a running backend + traffic — repros are in their sections.
5. Clean up `_t3_test_*` rows via the queries at the end of T3.21.
6. Commit + PR.

The static-check SQL bodies are mirrored in `src/scripts/check_*.py`. When the script gate goes red in CI, this report's structure mirrors what the gate will say.
