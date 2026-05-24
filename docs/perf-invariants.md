# Performance + Observability Invariants

## Performance + observability invariants

- **Cursor pagination on `/leads`.** `?limit=1..200` (default 50) +
  `?cursor=<opaque>`. Response: `{leads, next_cursor, has_more}`.
  Cursor is `base64url(json({c: created_at_iso, k: unique_key}))` —
  `_encode_lead_cursor` / `_decode_lead_cursor` in `backend/main.py`.
  Decoder fail-closed (malformed → `None` → page-1). Length-bounded
  (≤512 bytes raw, k ≤128). `SupabaseHelper.list_leads_recent(limit,
  cursor)` uses `created_at.lt.<c>` OR `and(created_at.eq.<c>,
  unique_key.lt.<k>)` — tie-break eliminates off-by-one on identical
  microsecond timestamps. Uses existing `idx_leads_created_at_desc`.
- **Async DB wrappers** in `SupabaseHelper` (`list_leads_recent`,
  `get_stats_rows`, `find_running_job`, `insert_orchestration_job`)
  wrap sync supabase-py `.execute()` in `asyncio.to_thread` so
  PostgREST round-trips don't block the uvicorn event loop. Background
  code in `_process_in_chunks` keeps direct sync calls — already off
  the request loop. Only `/leads`, `/stats`, `/process-lead`/
  `/process-all` hop through `to_thread`.
- **`/stats` cache** at `src/utils/stats_cache.py`.
  `_StatsCache(ttl_seconds=60.0)` with `asyncio.Lock` double-checked
  locking. 100 concurrent at expiry trigger exactly ONE rebuild.
  Per-uvicorn-worker — at `--workers N` you pay N builds per TTL.
  Invalidated by `process_csv_background` on successful upsert +
  orchestrator `_process_in_chunks` `finally` (every job exit).
  Single-lead `update_lead_info` / `update_audit` do NOT invalidate —
  operator edits can lag /stats up to 60s.
- **Cold-start lazy imports.** `backend/main.py` defers `pandas`,
  `AgenticRouter`, `ParallelAuditor`, `TaskOrchestrator`,
  `SupabaseHelper`, `export_leads`. Module `__getattr__(name)`
  resolves `db`/`router`/`auditor`/`orchestrator` on **attribute
  access on the module object** and caches into `globals()`.
  `pd.DataFrame` annotations are string-quoted + `TYPE_CHECKING` keeps
  the type hints meaningful. Result: `python -X importtime` 1.141s →
  219ms (-81%). **DO NOT** re-introduce eager construction of the
  singletons.
  - **PEP 562 trap (locked in 2026-05-22).** Module `__getattr__` does
    NOT fire for bare-name `LOAD_GLOBAL` lookups inside functions in
    the same module — only for `getattr(module, name)` /
    `module.name`. Handler code like `if not db.client:` would
    `NameError` if hit before `db` lands in `globals()`. The lifespan
    therefore runs a priming loop:
    ```python
    import sys as _sys
    _self = _sys.modules[__name__]
    for _name in ("db", "router", "auditor", "orchestrator"):
        try: getattr(_self, _name)
        except Exception as exc:
            logger.warning("Lazy global %s could not initialize: %s", _name, exc)
    ```
    Each `getattr` walks the attribute path → triggers `__getattr__`
    → caches the instance into `globals()`. After that, every
    handler's bare reference resolves via the normal globals lookup
    at zero cost. Per-name try/except so a partially-configured env
    (e.g. missing `GEMINI_API_KEY` → `router` init raises) only
    disables the affected feature instead of bricking the whole API.
    Any future lazy singleton added to `__getattr__` MUST also land
    in this loop.
- **Lifespan still blocks cold start.** `_self.db.check_schema()` +
  `_self.orchestrator.recover_interrupted_jobs()` run before uvicorn
  binds (note: explicit module attribute access — see PEP 562 trap
  above). Move `recover_interrupted_jobs()` into `asyncio.create_task`
  after `yield` to hit <5s on Render free tier. Follow-up.
- **Block-logger middleware** (`_block_logger_middleware`) logs
  `WARN slow handler: METHOD path took Nms` when elapsed ≥
  `SLOW_HANDLER_THRESHOLD_MS` (default 100 ms, env-overridable).
  Catches sync calls in async handlers. `loop.set_debug(True)`
  rejected — too costly. Use `PYTHONASYNCIODEBUG=1` in dev.
- **Web-vitals RUM** — `/metrics` endpoint accepts the
  `WebVitalsMetric` Pydantic model (Literal-allowlisted name,
  bounded value/rating/path/id). Browser sends via
  `navigator.sendBeacon` with a JSON `Blob` (bare `sendBeacon`
  defaults to `text/plain` and Pydantic 422s). Rate-limited 60/min.
  WARN for poor/needs-improvement, INFO for good. Frontend hook:
  `frontend/app/components/WebVitalsReporter.tsx`, mounted from
  `app/layout.tsx`.
- **Streaming `/export/download` and `/export/outreach`** use
  `StreamingResponse` + `_stream_leads_csv` async generator paging
  200 rows at a time via the keyset cursor. Memory bounded ≈60 KB
  per chunk. Inline `_csv_cell` injection guard. Column order LOCKED
  via `_EXPORT_FULL_COLUMNS` / `_EXPORT_OUTREACH_COLUMNS` tuples —
  adding a lead column doesn't auto-add to the export. Legacy
  `/export` (disk-write via `src/scripts/export_leads.py`) kept for
  CRM workflows; memory-bound by design.
- **Query profiler** at `src/utils/query_profiler.py`. Refuses without
  `QUERY_PROFILER=1`. Chainable proxy records verb + caller + timing.
  `assert_o1(per_unit=N, tolerance=2.0)` raises if any single caller
  exceeded `2*N` hits. Static audit of `src/` (2026-05-22) found
  ZERO O(N) N+1 patterns; profiler exists as a regression guard.
- **EnrichmentEngine shared-browser pool.** One Chromium per
  `EnrichmentEngine`; per-lead `new_context()`. `aclose()` MUST be
  called on teardown — invoked from `_process_in_chunks` `finally`
  and `_execute_deep_enrichment` `finally`. New direct callers must
  also `await engine.aclose()` or leak Chromium per job.
- **Load-test scaffolding** in `tests/loadtest/`:
  `locustfile.py` (3 scenarios A/B/C), `bench_enrich.py` (browser-pool
  A/B), `spike_locustfile.py` + `spike.sh` (0→100→0 trapezoid),
  `soak.sh` + `SOAK_PLAYBOOK.md` (24h driver + 8-signal monitoring
  playbook), `chaos.md` + `drop_supabase_pool.py` (3 scenarios,
  pool-drop is local-only via `CHAOS_LOCAL_ONLY=1`). Each VU injects
  a synthetic RFC1918 `X-Forwarded-For` — `_rate_limit_key` honors
  XFF only when `X-API-Key` validates, matching the Vercel/Render
  proxy pattern.
- **Structured JSON logging** (`src/utils/logging_config.py`). One
  JSON object per stdout line with the canonical envelope
  `{timestamp, level, logger, message, request_id, user_id, route,
  duration_ms?, exception?, <domain>...}`. Domain fields passed via
  `logger.info(msg, extra={"job_id": "..."})` merge at the top level
  (NOT nested under `"context": {…}`) — operator-facing `jq` queries
  in [docs/observability.md](docs/observability.md) §12 rely on the
  flat shape. `JsonFormatter` + `_CRLFScrubFilter` cooperate: filter
  scrubs `record.msg`, `record.args` (tuple OR dict form), AND any
  non-reserved `extra={}` key in `record.__dict__` — attacker-
  controllable values in any path can't smuggle a fake log line.
  Render's logs UI is grep-only, but JSON lines stay greppable
  (`grep '"level":"ERROR"' app.log | jq`). Sentry / Logtail / Loki
  parse the same envelope without an adapter. Pinned by
  `tests/test_logging_request_id.py::TestJsonFormatterEnvelope`
  (11 tests) + the existing
  `tests/test_crlf_injection.py::TestLoggingCRLFScrub`.
- **Request-context middleware**
  (`backend/main.py::_request_context_middleware`). Declared
  BEFORE `_block_logger_middleware`; under Starlette the LAST
  `@app.middleware('http')` decorator becomes the OUTERMOST wrapper
  (BaseHTTPMiddleware inserts at index 0), so `_block_logger`
  actually wraps `_request_context` at runtime. Functional outcome
  is unchanged: ContextVars set in the inner middleware are
  visible to the outer (Python ContextVars propagate down the
  async-call stack), and the slow-handler log emitted by
  `_block_logger` still carries `request_id` because the request
  Task hasn't ended yet. For every HTTP request:
  honours valid inbound `X-Request-ID` (`[A-Za-z0-9_-]{1,64}`),
  mints `uuid.uuid4().hex` otherwise; binds the three ContextVars
  (`request_id_var` / `user_id_var` / `route_var`); tags Sentry's
  per-request scope with `request_id` + `user.email`; propagates
  `X-Request-ID` on the response. **Critical: does NOT call
  `clear_request_context` in `finally`.** Each request runs in its
  own asyncio Task; the Context is GC'd cleanly on task end.
  Clearing eagerly would break `StreamingResponse` body iterators
  (e.g. `_stream_leads_csv`, `/operator/data-export`) — `call_next`
  returns when the response *object* is built; the body iterator
  runs *later* in the same task, and a cleared ContextVar would
  lose request_id on those log lines. Pinned by
  `tests/test_logging_request_id.py::TestRequestIdMiddleware`
  (7 tests).
- **`_block_logger_middleware`** logs `"slow handler"` with
  `extra={method, path, duration_ms, threshold_ms}` so duration_ms
  lands as a structured envelope field, not text-interpolated.
  Threshold default `SLOW_HANDLER_THRESHOLD_MS = 100`, env-overridable.

## Observability — Sentry

Backend + frontend both ship errors + transactions to Sentry. Full
wiring + verification procedure: [docs/observability.md](docs/observability.md).

- **Backend init** at module load in `backend/main.py` (between
  `logger = get_logger(__name__)` and the API-key block).
  `sample_rate=1.0` (errors), `traces_sample_rate=0.1`,
  `send_default_pii=False`. Skipped when `SENTRY_DSN` unset (dev
  stays clean). `before_send=_scrub_sensitive` strips `X-API-Key` /
  `X-Admin-Token` / `Authorization` / `Cookie` from
  `event["request"]["headers"]` AND drops the request body entirely
  on `/upload` (CSV is likely lead PII).
- **Frontend init** uses the `@sentry/nextjs@10.53.1` canonical
  layout: `frontend/instrumentation.ts` (Next.js server hook) →
  imports `sentry.server.config.ts` (Node) or `sentry.edge.config.ts`
  (Edge). `instrumentation-client.ts` handles the browser; reads
  `NEXT_PUBLIC_SENTRY_DSN`. `next.config.ts` wraps with
  `withSentryConfig(...)` so the webpack plugin uploads source maps
  at build (`SENTRY_AUTH_TOKEN` + `SENTRY_ORG` + `SENTRY_PROJECT`)
  with `sourcemaps: { deleteSourcemapsAfterUpload: true }` — maps
  resolve in Sentry, not on the CDN.
- **Release tag = git SHA**. Backend: `Dockerfile ARG GIT_SHA` →
  `ENV RELEASE_SHA`. `.github/workflows/deploy-backend.yml` passes
  `--build-arg GIT_SHA=${{ github.sha }}`. Frontend: build-time
  fallback chain in `next.config.ts`
  (`NEXT_PUBLIC_SENTRY_RELEASE → SENTRY_RELEASE → RENDER_GIT_COMMIT
  → "unknown"`).
- **`/_sentry/test`** endpoint (POST, X-API-Key required) gated by
  `SENTRY_TEST_ENABLED=1`. Returns 404 otherwise. Verification path
  in the launch checklist.
- **Tunnel route `/monitoring`** (configured via `tunnelRoute` in
  `withSentryConfig`) bypasses ad-blockers that hit `*.sentry.io`.
  Added to the public-path allowlist in
  `frontend/utils/supabase/middleware.ts` so unauthenticated client
  errors (crashes on `/login` before sign-in) still ship — exact-
  match-or-trailing-slash-subpath, same hardening as `/login` /
  `/auth` / `/api/auth`.
- **Per-request scope tag**: `_request_context_middleware` calls
  `sentry_sdk.set_tag("request_id", rid)` + (if email known)
  `sentry_sdk.set_user({"email": operator_email})` inside the per-
  request Sentry scope. Events captured during the request are
  filterable in Sentry UI by `tag:request_id:<rid>`.


## Alerting — Discord (5 signals to one channel)


Sentry handles uncaught exceptions + slow transactions. Five other
operational signals route to a single Discord channel via a shared
composite action. Full matrix + setup:
[docs/alerting.md](docs/alerting.md).

- **Composite action** `.github/actions/discord-notify/action.yml`
  — pure `curl` + `jq` + `bash`. No third-party action (no extra
  supply-chain surface). Inputs: `webhook-url`, `title`, `message`
  (Discord markdown), `severity` (critical/error/warning/info →
  embed colour), optional `link`. Empty `webhook-url` exits 0 with
  an actions warning — preview-PR runs without the secret stay
  green.
- **Five signals:**
  1. `synthetic-monitor.yml` — 3 consecutive failures of any of
     4 checks. State in a gist via
     `.github/scripts/synthetic-monitor.mjs::postAlert`, which
     prefers `DISCORD_WEBHOOK_URL` and falls back to
     `SLACK_WEBHOOK_URL` (the latter works against Discord's
     `/slack` endpoint too).
  2. `security.yml::storage-monitor` — `> 70 %` warning OR
     `> 90 %` critical. Severity decided by grep on the
     `storage_report.py` stdout for the code-quoted markers
     `HARD threshold` / `crossing soft threshold` (stable strings,
     not "70%" wording).
  3. `mutation-test.yml::aggregate` — kill rate below
     `MIN_KILL_RATE`. Discord ping + auto-updated tracker issue
     (label `mutation-coverage`).
  4. `cold-start-monitor.yml` — daily 04:00 UTC probe of `/`.
     Alerts on latency `>30 s` (`COLD_START_THRESHOLD_SECONDS`)
     OR non-2xx.
  5. `cert-expiry-monitor.yml` — weekly Mon 09:00 UTC.
     `openssl s_client` extracts `notAfter` from each host; alerts
     on `<30 days` (`CERT_EXPIRY_MIN_DAYS`) OR unreachable.
- **`cost-report.yml`** — weekly Mon 08:00 UTC. Runs
  `src/scripts/cost_report.py` which aggregates per-provider weekly
  spend (Supabase + Render + Maps + Domain; Gemini approximate
  until Google ships a billing API — digest has a prominent ⚠️
  banner noting the exclusion). Markdown digest posts to Discord +
  uploads as a 365-day-retention artifact for WoW comparison;
  baseline persisted in `.cost_baseline.json`.
- **Single secret**: `DISCORD_WEBHOOK_URL`. Optional per-host
  secrets for `cert-expiry-monitor` (`PROD_FRONTEND_HOST`,
  `PROD_BACKEND_HOST`) and `cold-start-monitor` (`PROD_BACKEND_URL`).

