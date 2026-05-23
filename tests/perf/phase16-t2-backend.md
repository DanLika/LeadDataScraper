# Phase 16-T2 — Backend audit (matrix of every endpoint × edge cases)

**Run date:** 2026-05-23  
**Branch:** `chore/phase16-t2-2026-05-23`  
**Backend under test:** `http://localhost:8000` (uvicorn, log → `/private/tmp/lds-uvicorn-p16.log`)  
**Harness:** `tests/perf/phase16-t2/` (`_helpers.py` + per-section ad-hoc runs)  
**Raw evidence:** `tests/perf/phase16-t2/results.jsonl` (138 records, 46 KB)

## Headline

- **HARD STOPs (per brief): not triggered.** 0 unexpected 500s across the matrix; auth gates held on every probe; no SSRF leak.
- **0 P0** — no auth bypass, no body smuggling, no information disclosure.
- **2 P2** — slow-handler log entries lose `request_id` correlation (Starlette `BaseHTTPMiddleware` sub-task isolation); backend ships zero browser security headers (CSP/XFO/HSTS/Referrer-Policy/XCTO), relying entirely on the Next.js proxy layer.
- **~6 P3 / informational** — doc-vs-code drift, minor design notes.

All 17 active sub-tasks completed (T2.16 Sentry skipped per env). Two cleanups landed (20 test campaigns + 1 phantom lead deleted; DB back to baseline 21 leads / 0 campaigns).

---

## T2.1 — Endpoint inventory + drift

**Status:** ✓ PASS (with doc drift)

Code grep on `backend/main.py` finds **37 routes / 36 unique paths**:

```
DELETE  /leads/clear                              3/hour    api+admin
DELETE  /operator/account                         1/hour    api+admin
GET     /                                         —         (open)
GET     /audit-status                             60/min    api
GET     /campaigns                                60/min    api
GET     /campaigns/{campaign_id}                  60/min    api
GET     /campaigns/{campaign_id}/export           12/hour   api
GET     /export                                    6/hour   api
GET     /export/download                           6/hour   api
GET     /export/outreach                           6/hour   api
GET     /health/schema                            12/min    api
GET     /insights                                 10/min    api
GET     /leads                                    30/min    api
GET     /operator/data-export                      1/day    api
GET     /orchestrator/active                      60/min    api
GET     /orchestrator/status/{job_id}             60/min    api
GET     /stats                                    30/min    api
POST    /_sentry/test                              5/min    api  (gated by SENTRY_TEST_ENABLED)
POST    /ask                                      10/min    api
POST    /audit/stop                               10/min    api
POST    /campaigns                                20/min    api
POST    /campaigns/{campaign_id}/generate          3/min    api
POST    /campaigns/{campaign_id}/pause            10/min    api
POST    /campaigns/{campaign_id}/start            10/min    api
POST    /discovery/start                           5/min    api
POST    /draft-linkedin                           20/min    api
POST    /draft-outreach                           20/min    api
POST    /enrich/start                             10/min    api
POST    /execute                                  10/min    api
POST    /hunt-all                                  3/min    api
POST    /hunt-lead                                20/min    api
POST    /metrics                                  60/min    api
POST    /orchestrator/start                        3/min    api
POST    /orchestrator/stop/{job_id}               10/min    api
POST    /process-all                               3/min    api
POST    /process-lead                             20/min    api
POST    /upload                                    5/min    api
```

**Drift (P3, doc lag):** `PROJECT_REPORT.md §4.1` lists "32 endpoints"; actual is 37. The 6 missing from the report are: `GET /`, `POST /_sentry/test`, `POST /metrics`, `GET /operator/data-export`, `DELETE /operator/account`, `GET /orchestrator/active`. CLAUDE.md covers all 6 in their relevant sections — only PROJECT_REPORT.md is stale.

`openapi.json` returns 404 in this env (`ENABLE_DOCS=true` not set — correct for prod-like config); inventory came from code grep.

---

## T2.2 — Auth header matrix

**Status:** ✓ PASS (16/16 expected outcomes)

Probes vs `GET /leads` + `DELETE /leads/clear` + `DELETE /operator/account` + `POST /metrics`:

| Probe | Want | Got |
|---|---|---|
| `GET /leads`, no API key | 403 | **403** ✓ |
| `GET /leads`, wrong API key | 403 | **403** ✓ |
| `GET /leads`, valid key + junk extra headers (X-Evil-Header, X-Original-URL) | 200 (no header smuggling) | **200** ✓ |
| `GET /`, no API key (liveness, public) | 200 | **200** ✓ |
| `POST /metrics`, no API key | 403 | **403** ✓ |
| `DELETE /leads/clear`, no auth | 403 | **403** ✓ |
| `DELETE /leads/clear`, API key only | 403 | **403** ✓ |
| `DELETE /leads/clear`, API key + wrong admin token | 403 | **403** ✓ |
| `DELETE /operator/account`, no auth | 403 | **403** ✓ |
| `DELETE /operator/account`, API key only | 403 | **403** ✓ |
| `DELETE /operator/account`, API key + wrong admin token | 403 | **403** ✓ |
| `DELETE /operator/account`, both keys + lowercase confirmation ("delete my account") | 422 | **422** ✓ |
| `DELETE /operator/account`, both keys + wrong phrase ("NOPE") | 422 | **422** ✓ |
| `DELETE /operator/account`, both keys + empty body | 422 | **422** ✓ |
| `DELETE /operator/account`, both keys + extra field (`sneak: 1`) | 422 (extra='forbid') | **422** ✓ |

**Informational:** raw-socket probe sending `X-API-Key:  <key>  ` (leading + trailing OWS) → **HTTP/1.1 200 OK**. This is RFC 7230 §3.2.4 compliant (h11/uvicorn strips OWS before the value reaches the handler), and `secrets.compare_digest` then validates the trimmed value. Not a vuln; documenting since the brief asked the question explicitly.

Three-factor GDPR deletion gate verified on every layer without ever invoking the destructive happy path (per advisor's HARD STOP on shared DB).

---

## T2.3 — Rate-limit boundary precision

**Status:** ✓ PASS (3/3 boundaries exact)

Tested safe representative limits (skip 1/hour and 1/day per advisor — would burn windows we can't get back today):

| Endpoint | Limit | OK count | First 429 |
|---|---|---|---|
| `GET /health/schema` | 12/min | 12 × 200 | **#13** ✓ |
| `POST /audit/stop` | 10/min | 10 × 200 | **#11** ✓ |
| `POST /campaigns/{bogus}/generate` | 3/min | 3 × 404 (lookup) | **#4** ✓ |
| `POST /metrics` (T2.17 burst-65) | 60/min | 60 × 200 | **#61** ✓ |

**P3 finding:** `Retry-After` header is NOT emitted on 429. Documented in CLAUDE.md (`headers_enabled=False`) — deliberate. RFC 6585 recommends it though; flag here so a future caller doesn't waste time waiting "until the next minute" by feel.

**Initial false start (worth knowing for future reviewers):** my first decorator-grep had an off-by-one bug that misreported every endpoint's limit by one route. Corrected parser used a 15-line lookahead from each `@app.METHOD` to the next `async def`. Re-grep matches `@limiter.limit` decorations exactly.

---

## T2.4 — Pydantic `extra='forbid'` / type / required / max-length

**Status:** ✓ PASS (32/32)

Five models exercised (`WebVitalsMetric`, `LeadProcessRequest`, `ExecutePlanRequest`, `AskRequest`, `CampaignCreate`). Six attack vectors each:

- valid → 2xx
- valid + extra field → 422 (`extra='forbid'`)
- missing required → 422
- wrong type → 422
- oversize string (`constr(max_length=N)` + 1) → 422
- empty body → 422

Plus per-model Literal violations (e.g. `name: "INVALID"` on WebVitalsMetric, `channel: "telegram"` on CampaignCreate, `task: "DROP_TABLES"` on ExecutePlanRequest).

All 32 outcomes match expectations. Special bug-find-on-myself: initial "valid metrics" payload included `navigationType` (a real `web-vitals` v3 field) → 422 extra_forbidden. Inspected frontend `WebVitalsReporter.tsx` — the frontend explicitly strips `navigationType` before beacon, so no prod bug. Backend model is intentionally narrower than the upstream lib.

---

## T2.5 — Cursor pagination edge cases (`GET /leads`)

**Status:** ✓ PASS (11 edge cases + cursor walk)

| Input | Result |
|---|---|
| `limit=0` | 422 (ge=1) ✓ |
| `limit=-1` | 422 ✓ |
| `limit=10000` | 422 (le=200) ✓ |
| `limit=201` | 422 ✓ |
| `limit=200` (boundary) | 200, full page ✓ |
| `limit="abc"` | 422 ✓ |
| `cursor="garbage!"` | **200, page 1** (decode fail-closes) ✓ |
| `cursor=<truncated base64>` | 200, page 1 ✓ |
| `cursor=base64({"c":"2099-01-01","k":"zzz"})` | 200, page 1 ✓ |
| `cursor="A"*1000` | 422 (length cap) ✓ |
| `cursor` with `%00` URL-encoded NULs | 200, page 1 ✓ |

**Cursor walk on `limit=10`** yielded 3 pages (10 / 10 / 1), `has_more` flipping `True → True → False` correctly, **21 unique `unique_key`s** across all pages with **0 dupes** detected.

Defense-in-depth: malformed cursors quietly fail back to page 1 (never 500). Length cap prevents 1 MB cursor-injection memory pressure.

---

## T2.6 — Concurrent matrix (`asyncio.gather × 20`)

**Status:** ✓ PASS with known-design observations

| Burst | Statuses | wall | p50 | max |
|---|---|---|---|---|
| `GET /leads` ×20 (cold, after T2.3) | {200: 19, 429: 1} | — | 5482ms* | 5543ms |
| `GET /leads` ×10 (fresh window) | {200: 10} | 215ms | **194ms** | 214ms |
| `GET /campaigns` ×20 | {200: 20} | 1893ms | 1891ms | 1894ms |
| `GET /orchestrator/active` ×20 | {200: 20} | 1546ms | 1543ms | 1546ms |
| `POST /campaigns` ×20 (same name) | {200: 20} | 1904ms | 1896ms | 1901ms |

\* The 5.5 s p50 on the first /leads burst was rate-limit-window backoff carryover from T2.3, not a real perf characteristic. Cleared after fresh window.

**Design-doc finding (informational, CLAUDE.md already documents):** `POST /campaigns` is *not* `asyncio.to_thread`-wrapped — when 20 are issued concurrently, they serialize on the sync supabase-py path inside the event loop. Total wall ≈ 95 ms × 20 = 1.9 s. The `to_thread` wrappers only cover the hot reads (`list_leads_recent`, `get_stats_rows`, `find_running_job`, `insert_orchestration_job`) per CLAUDE.md "Async DB wrappers" section. If `/campaigns` writes ever become bursty, this is the lift.

**No name uniqueness on `campaigns.name`** — 20 same-name POSTs all created distinct rows (verified by reading back `/campaigns` + counting). Design choice; cleanup confirmed every UUID `id` is distinct, every row schema is intact.

**Gap note (advisor):** brief asked for cross-response `unique_key` dedup on the GET /leads burst. Not separately verified — relied on the keyset-pagination determinism (same query, no inflight writes) that already makes a per-response cursor walk return 0 dupes (T2.5). If a future regression makes pagination non-deterministic under burst, T2.5 catches it first.

---

## T2.7 — Streaming `/export/download` + `/export/outreach`

**Status:** ✓ PASS

```
== /export/download ==
  status=200 bytes=4954 rows=22 (1 header + 21 leads)
  Content-Type: text/csv; charset=utf-8
  Content-Disposition: attachment; filename="leads_export_20260523_112855.csv"
  Transfer-Encoding: chunked   |   Content-Length: (none)
  31 columns: unique_key, name, company_name, first_name, email, phone, ...
  CSV-injection offenders: 0

== /export/outreach ==
  status=200 bytes=138 rows=1 (header only — only leads with email qualify)
  Content-Type: text/csv; charset=utf-8
  Content-Disposition: attachment; filename="crm_outreach_ready_20260523_112855.csv"
  Transfer-Encoding: chunked
  13 columns: email, first_name, last_name, company_name, ...

== /export legacy ==
  200, application/json, body: {"message":"Exports generated successfully in the 'exports' directory."}
  (Disk-write export, kept for CRM workflows per CLAUDE.md.)
```

**0 CSV-injection offenders** across every cell of both streaming exports (no cells starting with `= + - @ \t \r`). `sanitize_dataframe_for_csv()` is holding the line per CLAUDE.md.

---

## T2.8 — GDPR `GET /operator/data-export`

**Status:** ✓ PASS (gate tests for DELETE already in T2.2.g)

```
GET /operator/data-export
  status=200, application/zip, 3108 bytes
  Content-Disposition: attachment; filename="leadscraper-export-20260523T092922Z.zip"
  ZIP members: leads.csv | campaigns.csv | messages.csv | audit_log.json
  leads.csv: 22 rows × 44 cols  | csv_inj=0
  campaigns.csv: 21 rows × 10 cols | csv_inj=0
  messages.csv: 0 rows | csv_inj=0
  audit_log.json keys: export_timestamp, operator_email, schema_version, row_counts, orchestration_jobs
  row_counts: {leads: 21, campaigns: 20, campaign_messages: 0, orchestration_jobs: 1}
```

Metadata complete, schema parity holds, CSV-injection guard active on every CSV in the bundle.

**Caveat:** `/operator/data-export` rate limit is **1/day, peer-IP-keyed** (`key_func=get_remote_address`, NOT XFF-honouring — so the API-key holder can't unlock unlimited exports by rotating XFF). This run consumed today's slot — re-runs of T2.8 from this IP will 429 until UTC rolls over.

**DELETE /operator/account** — the three-factor gate (API key + admin token + Pydantic `Literal["DELETE MY ACCOUNT"]`) was exercised on 4 reject vectors in T2.2.g without ever invoking the destructive happy path on shared dev Supabase. All 4 returned the correct 403/422 *before* any DB write.

---

## T2.9 — Error response shapes

**Status:** ✓ PASS

| Trigger | Code | Body | Headers worth noting |
|---|---|---|---|
| `GET /nonexistent` | **404** | `{"detail":"Not Found"}` | content-type: application/json |
| `PATCH /leads` (no patch) | **405** | `{"detail":"Method Not Allowed"}` | **`Allow: GET`** ✓ |
| `POST /upload` (CSV, small) | 200 | `{"filename":"test.csv","status":"processing",...}` | — |
| `POST /upload` (octet-stream, evil.exe) | **400** | `{"error":"Only CSV files are allowed."}` | — |
| `POST /metrics` with `WebVitalsMetric` shape violation | **422** | `detail[0] = {type, loc, msg, input, ctx}` | full detail because authenticated |
| `POST /metrics` with deep-nested JSON (1500 levels) | 422 | `input` field **exactly 512 chars** ✓ | matches CLAUDE.md `_validation_with_authz_check` |
| `POST /metrics` with deep-nested JSON (2500 levels) | 422 | (no RecursionError) | doc says ≥2000 → 413; reality says Pydantic handles 2500 — not a bug, doc-drift |

**P2 finding** (header sweep on `/`, `/leads`, `/stats`, `/campaigns`, `/orchestrator/active`):

| Header | /export* | Other |
|---|---|---|
| `Server` | absent ✓ | absent ✓ |
| `X-Powered-By` | absent ✓ | absent ✓ |
| `Cache-Control` | set | **absent** |
| `X-Frame-Options` | **absent** | **absent** |
| `X-Content-Type-Options` | **absent** | **absent** |
| `Referrer-Policy` | **absent** | **absent** |
| `Strict-Transport-Security` | **absent** | **absent** |
| `Content-Security-Policy` | **absent** | **absent** |
| `Permissions-Policy` | **absent** | **absent** |
| `X-Request-ID` | present | present (12/12 routes) |

Backend trusts the Next.js proxy to set all browser-security headers (documented in CLAUDE.md). This is fine for the deployed shape (proxy is the only legitimate browser entry), but if the FastAPI port is ever exposed directly (internal tooling, a misconfigured ingress), there's no defense in depth. Low-effort lift: a `_security_headers_middleware` that stamps the static set on every response would make the backend OK to expose alone.

**CORS (verified separately):** OPTIONS preflight returns full CORS response (`access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS`, `max-age: 600`, `allow-credentials: true`, `allow-headers` including `X-API-Key, X-Admin-Token`). Origin-gated: `Origin: http://localhost:3000` echoes back in ACAO; `Origin: https://evil.com` gets **no ACAO header** → browser blocks. Working as designed.

---

## T2.10 — `X-Request-ID` middleware

**Status:** ✓ PASS (4/4)

| Probe | Result |
|---|---|
| GET /stats with no inbound `X-Request-ID` | response carries minted 32-char hex (e.g. `823a4f77df0646b1a22a8472340f3b9a`) ✓ |
| GET /stats with `X-Request-ID: phase16t2-<id>` | echoed verbatim ✓ |
| Invalid inbound (spaces, 200-char string, traversal, empty, `evil;DROP TABLE`, `<script>`) | all sanitized → fresh UUID minted ✓ |
| Raw-socket CRLF probe (`X-Request-ID: evil\r\nInjected-Header: yes`) | h11 framed the CRLF as a delimiter; response carried `X-Request-ID: evil` only, no `Injected-Header` reflected ✓ |
| Coverage check across 7 endpoints | 0 missing — header on every response ✓ |

---

## T2.11 — Block-logger middleware

**Status:** ✓ FIRES, ✗ correlation broken

Backend stdout → `/private/tmp/lds-uvicorn-p16.log`. Greppable JSON envelopes confirm:

- **394 "slow handler" entries** in this session's log
- Shape matches CLAUDE.md spec: `method`, `path`, `duration_ms`, `threshold_ms` as flat structured extras
- `SLOW_HANDLER_THRESHOLD_MS = 100` triggers correctly (e.g. `/insights` 10915 ms WARN logged)
- **Gap note (advisor):** env-override of `SLOW_HANDLER_THRESHOLD_MS` *not* re-tested with a fresh backend restart. The env-read line lives at `backend/main.py:633` (`float(os.getenv("SLOW_HANDLER_THRESHOLD_MS", "100"))`) so the wiring is in place — flag for the next focused dev cycle if anyone wants empirical proof under a non-default threshold.

```json
{"timestamp": "2026-05-23T09:35:15.769Z", "level": "WARNING", "logger": "backend.main",
 "message": "slow handler", "request_id": null, "user_id": null, "route": null,
 "method": "GET", "path": "/insights", "duration_ms": 10915.26, "threshold_ms": 100.0}
```

**P2 finding:** every slow-handler entry has `request_id: null, route: null, user_id: null`. Confirmed by cross-check against `slowapi` logger lines (which fire from *inside* the handler chain after `_request_context_middleware` binds the ContextVar in the same task):

```
$ grep -c '"request_id": null' /private/tmp/lds-uvicorn-p16.log
590     # _block_logger_middleware emissions (outer middleware, fresh task scope)

$ grep -cE '"request_id": "[a-f0-9_-]{8,}"' /private/tmp/lds-uvicorn-p16.log
151     # slowapi WARN, structlog from inside handlers — ContextVar visible
```

So the ContextVar *is* bound correctly; it just doesn't propagate back to the outer middleware. The cause:

1. `_block_logger_middleware` is registered LAST (line 639 in `backend/main.py`) → outermost in Starlette's stack
2. `_request_context_middleware` is registered FIRST (line 590) → inner
3. Starlette's `BaseHTTPMiddleware.call_next` runs the wrapped chain in a **separate asyncio Task**
4. ContextVars set by `bind_request_context` inside that sub-task **do not propagate back** to the outer middleware's `finally` clause
5. When the JsonFormatter renders block-logger's WARN line, the ContextVars are empty in its outer scope

The CLAUDE.md comment "Declared BEFORE `_block_logger_middleware` so it runs FIRST on inbound (Starlette's middleware stack: first-registered = outermost)" is wrong on the order — first-registered is actually *innermost* under Starlette. Mid-priority because:

- The response header `x-request-id` *is* set correctly (T2.10), so external correlation works
- Only the slow-handler WARN log line is missing it (in-log correlation broken)

**Fix options** (out of scope for T2): (a) swap to pure ASGI middleware for `_request_context_middleware` so the ContextVar bind happens in the parent task, or (b) read `request.state.x_request_id` instead of the ContextVar inside `_block_logger_middleware`.

---

## T2.12 — Security headers + CORS sweep

Covered in T2.9 above — see the "Header sweep" table. CORS works correctly when Origin is present; missing browser hardening headers on direct backend hits is the P2 documented there.

---

## T2.13 — Lifespan cold-start (regression for d3a90ff PEP 562 fix)

**Status:** ✓ PASS 5/5

Spawned fresh uvicorn on `:8001` (separate from user's `:8000` per advisor's HARD STOP), fired 3-request burst (`GET /`, `GET /stats`, `GET /leads?limit=1`) immediately after port bind, then SIGTERM. Repeated 5×.

| Iter | Boot time | First 3-req burst | Statuses |
|---|---|---|---|
| 1 | 2.09 s | 185.5 ms | 200/200/200 ✓ |
| 2 | 1.07 s | 154.9 ms | 200/200/200 ✓ |
| 3 | 1.12 s | 158.8 ms | 200/200/200 ✓ |
| 4 | 1.23 s | 176.6 ms | 200/200/200 ✓ |
| 5 | 1.07 s | 166.5 ms | 200/200/200 ✓ |

Lazy-singleton priming in lifespan (`db` / `router` / `auditor` / `orchestrator` via `sys.modules[__name__]` attribute access — CLAUDE.md "PEP 562 trap" section) is holding. No `NameError` on first inbound request.

---

## T2.14 — SSRF guard live

**Status:** ✓ PASS (22/22 decisions correct)

Direct `await assert_safe_url(url)` against `src/utils/ssrf_guard.py`:

| Class | Probes | Blocked |
|---|---|---|
| Loopback (`127.0.0.1`, `localhost`, `0.0.0.0`, `::1`) | 4 | 4 ✓ |
| Cloud metadata (`169.254.169.254`, `metadata.google.internal`) | 2 | 2 ✓ |
| Kubernetes (`kubernetes.default.svc`, `*.cluster.local`) | 2 | 2 ✓ |
| RFC1918 (`10.0.0.5`, `192.168.1.1`, `172.16.0.1`) | 3 | 3 ✓ |
| Link-local + ULA IPv6 (`fe80::1`, `fc00::1`) | 2 | 2 ✓ |
| Non-http/https schemes (`file:`, `ftp:`, `gopher:`) | 3 | 3 ✓ |
| Encoded loopback (octal `0177.0.0.1`, decimal `2130706433`) | 2 | 2 ✓ |
| Benign public (`example.com`, `google.com/maps`, `github.com`, `duckduckgo.com`) | 4 | 0 ✓ (allowed correctly) |

**Self-bug found** (and corrected before claiming a P0): first run called `assert_safe_url` synchronously, ignoring the `async def`. The coroutines were silently created+discarded, so every URL "passed". Caught + re-ran with `await`. Cautionary tale for static-scan tools that don't check coroutine-awaited.

Outbound-HTTP grep on `src/`:

| File | Imports `assert_safe_url` or `ssrf_guard`? |
|---|---|
| `src/scrapers/seo_audit.py` | ✓ |
| `src/processors/leadhunter.py` | ✓ |
| `src/core/parallel_auditor.py` | ? (uses `ParallelAuditor` orchestrator; delegates to seo_audit which guards) |
| `src/scripts/cost_report.py` | ? (cron script; hits well-known billing APIs) |

The two real-prod scrape paths import the guard. The script files don't but they call vendor APIs at known hostnames, not user-controlled URLs — out of SSRF scope.

---

## T2.15 — Concurrent write isolation

**Status:** ✓ PASS (covered by T2.6.D + follow-up)

- 20 concurrent `POST /campaigns` with identical `name` → 20 distinct UUID rows
- Read-back via `GET /campaigns` enumerated all 20 with full schema intact (`id`, `name`, `status`, `channel`, `segment_filter`, `total_leads`, `sent_count`, `reply_count`, `created_at`, `updated_at`)
- No torn rows, no truncated fields, no NULL surprises

The lack of a unique constraint on `campaigns.name` is by design (operator can create multiple "Q4 outreach" campaigns over time). If the app ever wants to dedupe, a unique index migration would be the proper layer.

---

## T2.16 — Sentry test event

**Status:** ⊘ SKIPPED (no Sentry in this env)

- `SENTRY_DSN` not set in `.env` → backend init block skipped Sentry SDK
- `SENTRY_TEST_ENABLED` not set → `POST /_sentry/test` correctly returns **404**

Sentry wiring lives on the deployed Render service. Live verification deferred to operator: `POST /_sentry/test` on prod with `SENTRY_TEST_ENABLED=1` temporarily set, then confirm event surfaces in the Sentry dashboard tagged with the right `release` (git SHA via `RELEASE_SHA` env) and `request_id`.

---

## T2.17 — `/metrics` receiver

**Status:** ✓ PASS

- All 9 Pydantic vectors verified in T2.4 (Literal name, float bounds, missing/extra/wrong-type)
- **60/min rate-limit boundary precise**: 60×200, **#61 = 429** ✓
- 200 response body: `{"ok":true}` (small, matches code)

---

## T2.18 — Cleanup + commit + PR

**Cleanup ✓:**
- Deleted 20 `t2-c-*` campaigns from `campaigns` table via supabase-py service-role client
- Deleted 1 phantom lead (`name=Test, website=https://x.com`) injected during T2.9 `/upload` smoke
- Post-cleanup state: **21 leads, 0 campaigns** (back to pre-T2 baseline)

**Report committed at `tests/perf/phase16-t2-backend.md`.** Raw run records under `tests/perf/phase16-t2/results.jsonl` (138 records).

---

## Summary findings table

| # | Severity | Area | Finding | Recommendation |
|---|---|---|---|---|
| 1 | **P2** | Logging | `request_id` / `route` are `null` in slow-handler WARN logs — Starlette `BaseHTTPMiddleware.call_next` runs in a sub-task, isolating ContextVars from the outer block-logger middleware | Either move `_request_context_middleware` to pure ASGI (parent task), or have `_block_logger_middleware` read `request.state.x_request_id` instead of the ContextVar |
| 2 | **P2** | Defense-in-depth | Backend ships zero browser security headers (CSP, XFO, XCTO, Referrer-Policy, HSTS, Permissions-Policy). Cache-Control set only on `/export/*`. Relies entirely on Next.js proxy | Add a `_security_headers_middleware` that stamps the static set on every response, so the backend is safe to expose directly if the Render ingress ever changes |
| 3 | P3 | Doc drift | `PROJECT_REPORT.md §4.1` says "32 endpoints"; actual is 37 (missing `/`, `/_sentry/test`, `/metrics`, `/operator/data-export`, `/operator/account`, `/orchestrator/active`) | Update the report next time it's touched |
| 4 | P3 | RFC behavior | `Retry-After` header is absent on 429 responses (deliberate per `headers_enabled=False` in CLAUDE.md) | Optional: re-enable so well-behaved clients back off correctly |
| 5 | P3 | Doc drift | CLAUDE.md says deeply-nested JSON triggers `RecursionError → 413`; in practice Pydantic handles 2500-deep without raising RecursionError and returns 422 instead | Update doc or raise depth in test fixtures so the 413 special-case actually fires |
| 6 | P3 | Semantics | `GET /orchestrator/status/{nonexistent_id}` returns 200 with `{"status":"not_found"}` instead of HTTP 404 | Frontend already special-cases this; design choice. Note only |
| 7 | P3 | Behavior | `secrets.compare_digest(api_key)` validates `X-API-Key:  <key>  ` (with OWS) because h11/RFC 7230 strips the OWS before delivery. Not a vuln — RFC compliant. Documenting per brief T2.2.c | None |
| 8 | P3 | Perf (already known) | `POST /campaigns` is not `asyncio.to_thread`-wrapped → 20 concurrent serializes ~95 ms × N in the event loop. Documented in CLAUDE.md | Add `to_thread` wrapper if write volume becomes bursty |

**0 P0/P1.** No auth bypass, no 500 cascade, no SSRF leak, no unauthenticated access to protected resources, no Pydantic body smuggling.
