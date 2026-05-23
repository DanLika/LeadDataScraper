# BookBed crossover — hardening patterns from LeadDataScraper

**Purpose.** LeadDataScraper (LDS) is internal tooling — the real SaaS revenue surface is BookBed.
LDS has absorbed 100+ hours of hardening (security tests, CI gates, prompt safety, header guards,
data integrity). This doc is the **gap analysis** that decides which patterns get ported to the
two BookBed repos, which are already covered there, and which don't apply.

**Targets.**
- `bookbed-website/` — Next.js 16 + React 19 marketing/comparison site, deployed on Firebase
  App Hosting (Cloud Run). Static SSG + 1 Server Action (`/tools/ical-checker`). Resend used
  only via mailto forms. **Trust boundary: public web crawlers + AI agents + form submits.**
- `bookbed/` — Flutter app (iOS / Android / Web) + Firebase Cloud Functions (TypeScript). Real
  SaaS. Firestore + Stripe LIVE MODE + Resend (transactional email) + `firebase_ai`
  (Gemini chat). **Trust boundary: authenticated owner accounts + their booked guests.**

**Not in scope.** LDS lead-gen pipeline (Playwright scrapers, Gemini agentic router, SEO audit,
discovery engine, outreach scoring, enrichment) — none of these touch BookBed surfaces.

**Maintenance contract.** This doc is a snapshot. Re-verify any row before porting — both
BookBed repos move fast (`bookbed-website` CLAUDE.md last touched 2026-05-12, `bookbed/`
2026-05-22). Spot-check the actual file, not the doc, before declaring "already done."

**Drift policy.** Update this doc when **a)** a new LDS hardening pattern lands that would
materially close a BookBed gap, or **b)** a BookBed surface adds a vector LDS already addresses
(new SMTP send path, new authenticated read endpoint, new file upload). Otherwise leave it
alone — three-repo doc duplication is a maintenance tax.

---

## Quick scoreboard (port effort vs. value)

| # | Bucket | bookbed-website | bookbed (Flutter + CF) |
|---|---|---|---|
| 1 | CI workflow set | **Critical gap** — 1 of LDS's 19 | **Moderate gap** — 3 of LDS's 19 |
| 2 | Cross-applicable security | Mostly already done | Mixed — email guards + log scrub worth porting |
| 3 | Prompt-injection fence | N/A — no LLM on site | **Worth porting** — Gemini chat over user input |
| 4 | Lead-gen specific | N/A | N/A |

**Recommended porting order:** website CI (Phase A) → CF email guards on Resend (Phase B) →
Flutter Gemini chat injection-fence (Phase C). Everything else is judgment-call or N/A.

---

## Section 1 — Lead-gen surface (LDS-only, NOT crossover)

Listed to make the boundary explicit. Do **not** port any of these.

| LDS file/module | Reason it stays LDS-only |
|---|---|
| `src/scrapers/discovery_engine.py` | Google-Maps scrape; BookBed doesn't acquire leads via crawl. |
| `src/scrapers/enrichment_engine.py` + shared Chromium pool | No Playwright on either BookBed surface. |
| `src/scrapers/seo_audit.py` + tech-stack detection | Lead-evaluation step, not user-facing on BookBed. |
| `src/core/agentic_router.py` + `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION` | LDS-specific tool routing. Pattern (UNTRUSTED_DATA fence) DOES transfer to `bookbed/ai_chat_provider.dart` — see row 3.1. |
| `src/core/task_orchestrator.py` + recovery + `_process_in_chunks` | LDS background-job system; BookBed uses CF triggers. |
| `src/processors/leadhunter.py` + outreach scoring + `segment_lead` regex | Lead workflow. |
| `src/utils/csv_helper.py` `sanitize_dataframe_for_csv` (formula injection) | BookBed has no CSV export to operators. Skip. |
| `src/utils/supabase_helper.py` lazy singletons + `asyncio.to_thread` | Supabase-specific; BookBed uses Firestore. |
| `src/utils/stats_cache.py` + `query_profiler.py` | Backend-internal perf primitives. |
| `frontend/utils/loginThrottle.ts` | Supabase Auth flow; BookBed uses Firebase Auth (different surface). |
| `frontend/utils/url.mjs::sanitizeNext` + open-redirect tests | No `/login?next=` flow on bookbed-website (mailto-only forms) and no `?next=` consumer on Flutter. |

---

## Section 2 — Cross-applicable security patterns (the actual gap table)

Status legend: ✅ already implemented · ⚠️ partial · ❌ missing · N/A threat doesn't apply

### 2.1 — HTTP / response-header hardening

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| CSP `script-src 'self'` (prod) | `frontend/next.config.ts` | ⚠️ `'unsafe-inline'` retained (SSG nonces impossible) — boundary closed at JsonLd escape | N/A (CF returns JSON only) | Different threat model; bookbed-website pattern is correct for its constraint. |
| CSP `connect-src` allowlist | `frontend/next.config.ts` | ✅ verified — Mux + Vercel + Tawk + fonts | N/A | bookbed-website allowlist is broader (more 3rd-party) but each entry justified per doc. |
| CSP `frame-ancestors 'none'` + `X-Frame-Options: DENY` | `frontend/next.config.ts` | ✅ both set | N/A | |
| HSTS preload | `frontend/next.config.ts` | ✅ `max-age=31536000; includeSubDomains; preload` | N/A | |
| `object-src 'none'` + `base-uri 'self'` + `form-action 'self' mailto:` | LDS missing these | ✅ verified in `next.config.mjs` | N/A | **bookbed-website is AHEAD of LDS here.** Backport to LDS as defense-in-depth — see Phase D below. |
| COOP / CORP / `X-Permitted-Cross-Domain-Policies` | LDS missing | ✅ all three set | N/A | **bookbed-website is AHEAD of LDS here.** Backport to LDS as defense-in-depth. |
| `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` | `frontend/next.config.ts` | ✅ all three | N/A | Permissions-Policy is broader on bookbed-website. |
| `poweredByHeader: false` | LDS frontend implicit (Next 16 default) | ✅ explicit | N/A | |
| `Cache-Control: private, no-store, max-age=0` + `Vary: Cookie` on HTML routes | `frontend/next.config.ts::pageNoCacheHeaders` | N/A — no auth pages on marketing site | N/A | |
| Strip upstream `Server` header at proxy | `frontend/app/api/proxy/[...path]/route.ts` | N/A — no proxy layer | N/A | |

### 2.2 — JSON / template injection at the script-tag boundary

| Pattern | LDS source | bookbed-website | bookbed Flutter | Notes |
|---|---|---|---|---|
| JSON-LD escape (`<`, `>`, `&`, U+2028, U+2029) before injection | LDS doesn't emit JSON-LD | ✅ `components/ui/json-ld.tsx` — verified | N/A | bookbed-website is AHEAD here. LDS adds no JSON-LD to public HTML, so N/A. |

### 2.3 — Outbound HTTP / SSRF

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| SSRFGuardResolver: reject private/loopback/link-local/multicast IPs + cloud metadata hostnames | `src/utils/ssrf_guard.py` | ✅ **verified** — `app/[locale]/tools/ical-checker/actions.ts`: `https:`-only, credential strip, `dns.lookup` all-records private-IP reject (incl. CGNAT 100.64/10 + IPv6 ULA/link-local/mapped), double-resolve rebind guard, `redirect: "manual"`. Self-noted residual: undici may re-resolve post-guard (TOCTOU) — acceptable for a validator. | ✅ **verified 2026-05-23** — outbound surfaces: Twilio SMS (static `api.twilio.com`), Resend (static `api.resend.com`), iCal sync (`icalSync.ts:344` uses `validateIcalUrl()` SSRF guard + `maxRedirects: 5` cap on owner-supplied `ical_url`). iCal was the open question — already covered. | If a NEW outbound surface lands (operator webhooks, custom .well-known fetchers), port the LDS SSRF helper — or extend the existing `validateIcalUrl` resolver, which is already close to LDS parity. |
| Playwright route guard re-runs SSRF check on every subresource | `enrichment_engine.py` | N/A — no Playwright | N/A | |
| Block scheme allowlist (`http://`/`https://` only) | `frontend/utils/url.mjs::ensureProtocol` | ⚠️ probably implicit; verify on `safeHref` per CLAUDE.md | ⚠️ ? CF callable URLs | Worth porting `ensureProtocol` pattern if user-content links exist in `bookbed/` (booking confirmation pages, etc.). |

### 2.4 — Email / SMTP injection

| Pattern | LDS source | bookbed-website | bookbed CF (Resend) | Notes |
|---|---|---|---|---|
| Recipient regex anchored with `\Z` (not `$`) — rejects trailing `\n` | `src/integrations/email_sender.py` | N/A (mailto only) | **❌ Worth porting if Resend recipients come from user input** | LDS test `tests/test_crlf_injection.py` is the lock-in. |
| Subject + from_name CRLF reject before MIME header write | `src/integrations/email_sender.py` | N/A | **❌ Worth porting if subject contains user-controlled data (booking notes, guest name)** | Same test file. |
| Plaintext-body CRLF normalization (no header smuggling via body) | `src/integrations/email_sender.py` | N/A | **❌ Worth porting** | |
| All recipient/subject paths funnel through one guard module | LDS structural | N/A | **Action: introduce `functions/src/lib/emailGuards.ts` mirror** | Highest-value port on the Flutter side. |

### 2.5 — Logging / log-line forgery

| Pattern | LDS source | bookbed-website | bookbed CF | bookbed Flutter | Notes |
|---|---|---|---|---|---|
| CRLF scrub on `record.msg` AND every entry of `record.args` (tuple+dict) | `src/utils/logging_config.py::_CRLFScrubFilter` | N/A | **❌ Sentry integration in CF — if user-input lands in `logger.error(...args)` call, port the Node/TS equivalent.** | **⚠️ `LoggingService.logDebug/logError` is called with user text length only today** — verify no future `logError('AiChat: ' + text, ...)` calls land. | LDS pattern: filter at handler level, not at every call site. |

### 2.6 — CSRF / Origin gate

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| Fail-closed Origin allowlist on state-changing POSTs | `frontend/app/api/proxy/[...path]/route.ts` + `frontend/app/api/auth/signout/route.ts` | N/A — no auth state-change POSTs | ❌ **VERIFIED 2026-05-23 — App Check is NOT enforced.** All `onCall({...})` configs (audit across `availability.ts`, `emailVerification.ts`, `passwordReset.ts`, others) use `cors: true` only — ZERO `enforceAppCheck: true`. Per-handler `request.auth` checks exist; App Check (anti-bot, anti-script-runner) does not. **Real security gap, not a port — pure bookbed work**. Adjacent: `/tools/ical-checker` Server Action's in-process rate limit + DNS guard already cover its threat. | LDS gates Origin in code; CF gates via Firebase auth context. |
| Strip client-controlled XFF, re-emit from trusted proxy header | `frontend/app/api/proxy/[...path]/route.ts` + `_rate_limit_key` in `backend/main.py` (XFF only honored when API key valid) | ✅ `getClientIp()` rewrites trust the rightmost XFF (Cloud Run pattern) | N/A — App Check / auth.uid not IP-based | bookbed-website pattern is correct for App Hosting / Cloud Run. |

### 2.7 — Cookies / session

| Pattern | LDS source | bookbed-website | bookbed Flutter | Notes |
|---|---|---|---|---|
| Cookie-floor: `SameSite=Lax`, `HttpOnly=true`, `Secure=true` overwritten true-down (never weakened) | `frontend/utils/supabase/middleware.ts` | N/A — no session cookies (static site) | N/A — Firebase Auth manages tokens client-side | |
| 1157-case cookie-floor fuzz test | `frontend/utils/supabase/cookie-floor-fuzz.test.mjs` | N/A | N/A | |

### 2.8 — Input validation / payload pollution

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| Pydantic `extra='forbid'` + bounded `constr` + `Literal` enums on every BaseModel | `backend/main.py` + meta-test `tests/test_pydantic_models_meta.py` | N/A — no API surface | **❌ Worth porting** — CF callables use `data: any` by default. `functions/src/lib/validators.ts` with `zod` schemas + `strict: true` is the JS-side equivalent. | Highest leverage: every callable should reject extra fields. |
| 422 schema-leak gate (auth check inside RequestValidationError handler) | `backend/main.py::_validation_with_authz_check` | N/A | ⚠️ CF callables return generic 400 by default; only relevant if a custom errorMap leaks shape | Lower priority. |
| Deeply-nested JSON → 413 (RecursionError → "Payload nesting too deep") | `backend/main.py` exception handler | N/A — Next default | ⚠️ CF default body limit is 10 MB but no nesting limit — port if you start accepting nested structured input | |
| `NaN` / `Infinity` rejected at JSON parse | LDS via custom 422 stringify with `allow_nan=False` | N/A — Next default | ⚠️ JS `JSON.parse` accepts `Infinity` strings; verify schemas | |

### 2.9 — File upload guard

| Pattern | LDS source | bookbed-website | bookbed CF / Flutter | Notes |
|---|---|---|---|---|
| Stream + 50 MB cap with abort | `/upload` in `backend/main.py` | N/A | ⚠️ If CF or Flutter direct-Firebase-Storage uploads exist (property photos, etc.) — port the streaming + bytes-counted abort. | Check `storage.rules` + any client upload handlers. |
| Content-Type allowlist (no `application/octet-stream`) | `backend/main.py` upload | N/A | Same applies to Storage rules — content-type enforcement via rules. | |
| 30-vector `tests/test_upload_attacks.py` | LDS | N/A | Worth porting harness if BookBed has any operator upload path | |

### 2.10 — LLM prompt safety

| Pattern | LDS source | bookbed-website | bookbed Flutter (`ai_chat_provider.dart`) | Notes |
|---|---|---|---|---|
| `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>` fence + paired `system_instruction` | `src/core/agentic_router.py::_fenced_json` + `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION` | N/A | **❌ Missing — user message goes raw to `_chatSession.sendMessageStream(Content.text(text.trim()))`** with only static system instruction (KB markdown). | High priority — see Phase C. |
| Strip literal `</UNTRUSTED_DATA>` substring before embedding | `agentic_router.py` | N/A | **❌ Missing** | Same row. |
| Blocked-keyword pre-filter for off-topic | LDS doesn't have this | N/A | ✅ `_blockedKeywords` list (33 terms) | bookbed Flutter is AHEAD here — patterns differ. |
| Language detection for response routing | LDS doesn't have | N/A | ✅ `_detectLanguage` HR/EN | |
| Daily message cap per user | LDS doesn't have | N/A | ✅ 30/day | |
| Refusal classification: 4 buckets (refusal/benign/foreclosed/dangerous) | `tests/test_refusal_boundaries.py` — 6 malicious instructions × judge classifier | N/A | **❌ No refusal test suite** — port at least the test harness. | Live API key tier. |
| Prompt SHA256 snapshot test (drift catches in CI) | `tests/test_prompt_snapshots.py` + `tests/fixtures/prompt_snapshots.json` | N/A | **❌ The KB markdown is the prompt — snapshot `assets/kb/bookbed_knowledge_base.md` SHA in CI.** | Easy port. |
| Hallucination guard (sparse-lead fixtures + judge) | `tests/test_outreach_hallucination.py` | N/A | **❌ Different shape** — BookBed risk is "Gemini fabricates BookBed feature that doesn't exist." Adapt: ground every claim to the KB. | High priority. |

### 2.11 — Error handling / fingerprinting

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| Global exception → JSON `{"error":"Internal server error"}` 500 | `backend/main.py` | N/A | ⚠️ CF default returns plain text on uncaught throw — port to `functions/src/lib/errorHandler.ts` | |
| `RecursionError` → 413 special-case | `backend/main.py` | N/A | ⚠️ Same wrapper | |
| 18-regex sensitive-substring scrape over fault-injected errors | `tests/test_error_message_leak.py` | N/A | **❌ Worth porting** — harness reads CF responses, asserts no `node_modules` paths, DB URLs, etc. leak. | |
| Liveness probe returns no version/product metadata | `backend/main.py` `/` | N/A | ⚠️ CF doesn't typically expose health endpoints, but if any internal probe exists, check. | |

### 2.12 — Rate limiting

| Pattern | LDS source | bookbed-website | bookbed CF | Notes |
|---|---|---|---|---|
| In-process per-key token bucket | `frontend/utils/loginThrottle.ts` + slowapi on backend | ✅ **verified** — `actions.ts`: 10/min trusted, stricter 3/min for the shared `unknown` bucket, opportunistic GC at `rateBuckets.size > 5000` | ⚠️ Per `.claude/rules/cloud-functions.md` rate-limit pattern referenced; **verify all callable endpoints have it** | LDS pattern: bounded `MAX_BUCKETS` cap + LRU evict + expired-sweep. bookbed-website's GC is sweep-only (no hard cap / LRU evict) — under a unique-IP flood the map can still grow to 5000 before a sweep. Minor; port LDS's hard-cap evict if that surface ever gets hostile traffic. |
| Trusted client-IP header derivation | LDS proxy + bucket key | ✅ rightmost XFF on Cloud Run | App Check + `auth.uid` (not IP-based) | |

### 2.13 — Database integrity (Supabase/Firestore symmetric concepts)

| Pattern | LDS source | bookbed-website | bookbed Flutter / Firestore | Notes |
|---|---|---|---|---|
| RLS deny-all + service-role bypass | `supabase_schema.sql` + `src/scripts/schema_drift_check.py` | N/A | Firestore rules ARE the equivalent — bookbed has `firestore.rules` + `firestore-rules-drift.yml` workflow. | bookbed is on equivalent footing here; Firestore rules drift workflow IS the pattern. |
| Schema drift CI gate | `src/scripts/schema_drift_check.py` + `ci.yml::schema-drift` | N/A | ✅ `firestore-rules-drift.yml` per repo workflow listing | Different stack, same idea. |
| Referential integrity probe (FK cascade + violation) | `src/scripts/check_referential_integrity.py` | N/A | ⚠️ Firestore has no FK; replace with "deleting parent doc removes orphaned subdocs" probe — port if `booking_services` cleanup is mission-critical (per CLAUDE.md it was a 2026-05-22 cleanup target). | |
| Orphan + zombie sweep + 4h auto-heal stale jobs | `src/scripts/check_orphans_and_zombies.py` | N/A | ⚠️ Adapt: stale-pending-bookings sweep at 4h. Worth porting if `atomicBooking.ts` ever leaves rows in inconsistent state. | |
| NULL ratio audit | `src/scripts/check_null_audit.py` | N/A | N/A — Firestore is schemaless, NULL ratio is not the right metric | |
| `statement_timeout` per role + cancellation primitive verify | `src/scripts/check_statement_timeouts.py` | N/A | N/A — Firestore has no equivalent (per-request timeout is built-in) | |
| CHECK constraints + drift parity | `supabase_schema.sql::add_check_constraints` | N/A | ⚠️ Firestore rules can encode similar invariants (`is string`, `is number > 0`, allowlist string values) — review whether `firestore.rules` enforces equivalent constraints to LDS's 10 CHECKs. | Worth a one-time audit but not a recurring port. |
| Backup PITR verification (monthly restore-to-branch) | `.github/workflows/backup-verify-deep.yml` (disabled by default) | N/A | ⚠️ Firestore has PITR; verifying restoration parity is high-value. Worth a manual quarterly drill. | |

---

## Section 3 — CI workflow set (the biggest gap)

LDS has 19 workflows. bookbed-website has 1. bookbed has 3.

### 3.1 — Workflow inventory + portability

| LDS workflow | What it gates | bookbed-website fit | bookbed (Flutter) fit |
|---|---|---|---|
| `ci.yml` (pre-merge PR gate, ~20 required checks) | unit tests, lint, typecheck, audit, secrets scan, lockfile sync | **❌ Port — adapt to npm-only + Next build** | ✅ partial (`ci.yml` exists); review against LDS's 20-check set. |
| `security.yml` (push + daily cron) | pip-audit, npm audit, semgrep, secrets scan, gitleaks | **❌ Port — npm audit + semgrep are direct** | **❌ Port — add npm audit + semgrep across `functions/` + Flutter analyzer + Dart format check** |
| `deploy-backend.yml` (push main → GHCR + SLSA + Render rollout on signed digest) | tagged supply chain | N/A (App Hosting auto-rollout from main) | N/A (Firebase deploy) |
| `release.yml` (tag `v*` → SLSA3 + cosign) | tagged release | N/A | ⚠️ Optional for Flutter — Firebase ships its own auth. |
| `e2e.yml` | Playwright across browsers | N/A — site is mostly static; sub-pages tested in dev | **❌ Worth porting** — `bookbed/` Flutter has integration tests but cross-device Playwright via web build would catch regressions. |
| `mutation-test.yml` (weekly, 80% kill rate on security-critical modules) | mutmut on SSRF/prompt-safety guards | N/A | **❌ Port — target `functions/src/atomicBooking.ts`, `availability.ts`, Stripe handlers — stryker.js equivalent.** |
| `flakiness-detector.yml` (3× parallel pytest nightly, gist + label) | catches flakes early | N/A | **❌ Port for `functions/` jest suite + `flutter test` — vitest 3× pattern.** |
| `workflow-drift.yml` (sha256 vs `.github/workflow-hashes.json`) | catches Studio hand-edits to workflows | **❌ Port — same threat applies, same script** | **❌ Port** |
| `pr-hygiene.yml` (Conventional Commits + PR size gate) | label discipline | **❌ Port (low effort)** | ✅ implicit, but explicit gate is better. |
| `dependabot-auto-merge.yml` | auto-merge patch deps after CI | **❌ Port — adapt to `bookbed-website` packages** | **❌ Port** |
| `cold-start-monitor.yml` | latency probe | N/A — App Hosting is auto-warmed | **❌ Port — CF cold starts are real (700ms+); ICR latency probe matters.** |
| `cost-report.yml` | weekly spend summary | N/A | **❌ Port — Firebase + Stripe + Resend cost sweep weekly is high-value at scale.** |
| `cert-expiry-monitor.yml` | TLS expiry probe | ⚠️ Firebase manages App Hosting certs but bookbed-website CLAUDE.md doesn't mention monitoring | ⚠️ Same |
| `synthetic-monitor.yml` | scripted user journey | N/A | **❌ Port — book-a-room E2E synthetic on production.** |
| `data-integrity.yml` (Supabase invariants) | RLS + grants + orphans + zombies + bloat + slow-query + analyze-freshness + JSONB shapes + null-audit + storage-growth | N/A | **Adapt** to Firestore equivalents — see Section 2.13 above. |
| `migration-safety.yml` (preview branch + drift check) | schema preview | N/A | ⚠️ Firestore rules migration via preview project — Firebase supports this on Blaze plan. |
| `post-deploy-smoke.yml` | smoke tests after deploy | **❌ Port** — 5-route HEAD + JSON-LD validity smoke after App Hosting rollout | **❌ Port** |
| `preview-smoke.yml` | PR preview env smoke | ⚠️ Firebase App Hosting preview channels — adapt | ⚠️ |
| `main-matrix.yml` (cross-platform tests) | OS/version matrix | ⚠️ Probably overkill | ⚠️ Already in Flutter `ci.yml`? |

### 3.2 — Workflow porting prerequisites

Before any LDS workflow ports cleanly, replicate these invariants on the target:
- **All `uses:` lines SHA-pinned** with `# vX.Y.Z` comment (Dependabot bumps both atomically).
- **Top-level `permissions: contents: read`** + explicit per-job escalations.
- **Standard concurrency block** (`group: ${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true` on PR-only workflows).
- **Fork-PR guard** on any workflow that runs untrusted code:
  ```yaml
  if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository
  ```
- **`workflow-pin-guard` pre-commit hook** to reject `uses: org/action@vN` patterns.

These are the load-bearing invariants. Skipping them turns "ported workflow" into "new attack surface."

---

## Section 4 — Test harness inventory (port selectively)

The LDS test suite is ~30 files under `tests/`. Most are LDS-specific. The ones worth porting:

| LDS test | Why port to BookBed | Adaptation effort |
|---|---|---|
| `tests/test_crlf_injection.py` | If `bookbed/functions/src/` ever sends Resend mail with user-controlled subject/recipient — this is the regression guard. | **Medium.** Rewrite in TypeScript + jest. Cover: regex anchored with `$` mode (JS default), CRLF in subject, CRLF in From name. |
| `tests/test_prompt_injection_corpus.py` (15-payload injection corpus) | Defends bookbed Flutter Gemini chat against jailbreak / sysprompt-leak attempts. | **Medium.** Port corpus to Dart integration test; assert canned-blocked or refusal. |
| `tests/test_redos.py` (subject parser + email regex input cap) | Catastrophic backtracking on user input is a 1-line outage. | **Medium.** Adapt to JS regex in CF + Dart regex in Flutter. Time-bound assert via `setTimeout(..., 100)`. |
| `tests/test_error_message_leak.py` (18-regex sensitive substring scrape) | Verifies CF errors don't leak `node_modules` paths, env values, DB URLs. | **Low.** Port the 18 regexes to a JS post-response scraper. |
| `tests/test_json_pollution.py` (prototype pollution, dup keys, control chars, deep nest) | Validates Pydantic-like guards on CF callable inputs after zod schemas land. | **Low** if zod schemas already exist; corpus port is mechanical. |
| `tests/test_timing_attack.py` (`secrets.compare_digest` empirical timing) | If bookbed CF ever validates a shared secret (HMAC webhook, API key) — use `crypto.timingSafeEqual`, lock it in with this. | **Low.** Mechanical port. |
| `tests/test_endpoint_hardening.py` (every endpoint × 7 concerns) | Meta-test: every new CF callable should auto-discover and probe these vectors. | **High.** Worth it for `functions/src/` once callable count > 20. |
| `tests/test_refusal_boundaries.py` (6 malicious instructions + judge classifier) | Locks in Gemini chat won't help with delete_data / scrape_private / etc. | **Medium.** Adapt prompts to BookBed context; reuse judge classifier. |

The rest (Supabase-specific drift checks, agentic-router behavior, Gemini cost budget for outreach
pipeline, segment-stability for `segment_lead` regex) are LDS-only.

---

## Section 5 — Phased action checklist

### Phase A — bookbed-website CI hardening (sequence)
**Why first:** website ships with 1 workflow vs LDS's 19. Highest gap-vs-effort ratio.

1. Copy `ci.yml` skeleton from LDS → strip Python/Supabase jobs → keep: npm audit, ESLint --max-warnings 0, tsc --noEmit, semgrep, gitleaks, lockfile-sync, license-check, Conventional Commits, PR size gate.
2. Copy `security.yml` → strip pip-audit/Supabase scripts → keep: npm audit moderate+, semgrep daily cron.
3. Add `workflow-drift.yml` + `.github/workflow-hashes.json` (regenerate via `make workflow-hashes` script copy from LDS).
4. Add `dependabot.yml` + `dependabot-auto-merge.yml`.
5. Add `post-deploy-smoke.yml` for App Hosting (5 routes × HEAD 200 + valid JSON-LD on `/`).
6. Adopt `workflow-pin-guard` pre-commit hook.

**Estimated effort:** 1 dev-day. Worth ~15× the time saved on first dependency CVE.

### Phase B — bookbed Flutter email guards on Resend (sequence)
**Why second:** Resend is live; user-controlled fields (guest name, booking notes) can land in subject/body today.

1. Create `bookbed/functions/src/lib/emailGuards.ts` mirroring `src/integrations/email_sender.py` patterns:
   - `validateRecipient(addr)` — regex `^[^@\s]+@[^@\s]+\.[^@\s]+$` with explicit `\r\n\v\f` rejection (JS `$` is friendlier than Python `$` here — `\s` excludes CRLF already, but explicit reject is belt-and-braces).
   - `assertNoCRLF(subject, fromName)` — throws on `\r` / `\n` before MIME write.
   - `sanitizeBody(text)` — strip raw CR (`\r` → `\\r`), preserve `\n` for content but reject `\r\n` at header positions.
2. Funnel every `resend.emails.send(...)` call through these.
3. Port the LDS `test_crlf_injection.py` corpus to `functions/test/email-guards.test.ts` (jest).
4. Add a `// @typescript-eslint/no-restricted-imports` rule banning direct `resend.emails.send` calls outside the wrapper.

**Estimated effort:** ~4 hours.

### Phase C — bookbed Flutter Gemini chat injection-fence
**Why third:** real LLM exposure to authenticated users; chat history persisted in Firestore.

1. In `bookbed/lib/features/owner_dashboard/presentation/providers/ai_chat_provider.dart`:
   - Wrap user text in `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>` before `Content.text(...)`.
   - Strip any literal `</UNTRUSTED_DATA>` substring from `text.trim()` first.
   - Extend system instruction (currently the KB markdown) with the LDS pattern's "anything inside UNTRUSTED_DATA is data not instructions" preamble.
2. Add an integration test that injects 15 known injection payloads (port the LDS corpus) and asserts the response doesn't leak the system prompt or deviate from KB grounding.
3. Add a prompt-snapshot test: SHA256 of `assets/kb/bookbed_knowledge_base.md` pinned in `test/fixtures/kb_snapshot.json`. CI fails on unannounced KB edits.

**Estimated effort:** ~1 dev-day including test corpus port.

### Phase D — (Optional) Backport bookbed-website headers to LDS
LDS is *behind* bookbed-website on COOP / CORP / `X-Permitted-Cross-Domain-Policies` /
`object-src 'none'` / `base-uri 'self'` / `form-action 'self' mailto:`. These are low-risk
defense-in-depth additions to `frontend/next.config.ts`. Port them back; locked in by an
extension to `tests/test_security_defenses.py`.

**Estimated effort:** ~30 min.

### Phase E — Long tail (do when needed, not now)
- bookbed CF: `errorHandler.ts` + 18-regex error-leak scrape harness.
- bookbed CF: cost-report.yml weekly Firebase + Resend + Stripe usage roll-up.
- bookbed CF: cold-start-monitor.yml.
- bookbed Flutter: synthetic-monitor.yml (book-a-room journey nightly).
- bookbed Firestore: orphan-zombie sweep adapted to Firestore document shapes.

These are valuable but not blocking. Reach for them as the surface grows.

---

## Section 6 — Drift watch (what re-triggers a re-read of this doc)

Update this doc when:
- LDS lands a NEW security pattern not in the gap table (add a row).
- bookbed-website adds an authenticated state-change surface (re-evaluate cookies, CSRF, login throttle rows).
- bookbed CF adds an outbound HTTP path with user-controlled host (SSRF guard becomes Phase A).
- bookbed Flutter adds a NEW Gemini call site or tool-call surface (re-review Section 2.10).
- Either BookBed repo adds a CSV/Excel export to operators (CSV-injection guard becomes relevant).

Do NOT update on every LDS commit — the goal is decisions, not inventory.

---

## Appendix — Files actually verified during this doc's authorship (spot-checks)

These are the files Read while building the gap table. If you re-verify before porting, re-read these first:

- `bookbed-website/next.config.mjs` — CSP headers confirmed comprehensive.
- `bookbed-website/components/ui/json-ld.tsx` — escape pattern confirmed.
- `bookbed-website/app/[locale]/tools/ical-checker/actions.ts` — SSRF guard confirmed (https-only, credential strip, double-resolve rebind guard, `redirect: "manual"`, 8s timeout, 5 MB cap, in-process rate limit). Note the real path is under the `[locale]` next-intl route group — `bookbed-website` CLAUDE.md documents it without the `[locale]` segment.
- `bookbed-website/.github/workflows/` — only `daily-publish.yml` present (gap confirmed).
- `bookbed/.github/workflows/` — `ci.yml`, `deploy-widget.yml`, `firestore-rules-drift.yml` (gap confirmed).
- `bookbed/lib/features/owner_dashboard/presentation/providers/ai_chat_provider.dart` — Gemini integration via `firebase_ai`; no UNTRUSTED_DATA fence; daily cap + blocked keywords + language detect already present.
- `bookbed/functions/package.json` — `resend ^6.9.2` confirmed; CF email send path is active.

Rows marked "verify" / "?" in the gap table were NOT spot-checked; treat them as
hypothesis-only until re-confirmed.

### Verification debt (spot-checked 2026-05-23 — was hypothesis, now resolved)

The 8 rows below were marked ⚠️/? in the original 2026-05-22 doc — file inspection
during Phase A+B execution resolved every one. Three turned out wrong; one is
better than hypothesized; the rest match the hypothesis but with path
corrections worth noting. Re-verify any of these before basing a port-PR
on the row.

| Row | Original claim | Verified status (2026-05-23) |
|---|---|---|
| 2.3 (SSRF) — bookbed CF | "outbound is only OTAs + Resend, both static-allowlisted hosts" | ⚠️ **PARTIAL — claim incomplete.** Real outbound: Twilio (`smsService.ts`, static `api.twilio.com`), Resend (static `api.resend.com`), **AND iCal sync** (`icalSync.ts` fetches owner-provided `ical_url` — *the* SSRF vector). The CF already has `validateIcalUrl()` SSRF guard + `maxRedirects: 5` cap at `icalSync.ts:344` — covered, but the doc previously claimed iCal wasn't an outbound surface. Update: bookbed CF already meets Phase A-level SSRF parity here. |
| 2.5 (log CRLF) — bookbed CF | "Sentry integration in CF — verify no `logger.error('...' + userInput, ...)` pattern" | ⚠️ **GAP.** Module is at `functions/src/logger.ts` (NOT `lib/logger.ts`). `Logger.info/debug/warn` proxies to `functions.logger.*` which emits JSON to Cloud Logging — JSON encodes `\r\n` literally, so log-line forge does NOT work on the JSON wire. But any downstream gcloud-CLI text-format consumer would re-decode. Worth porting `_CRLFScrubFilter`-equivalent if any text-format consumer ever reads CF logs. **Lower-risk than LDS file logger today.** |
| 2.5 (log CRLF) — bookbed Flutter | "`LoggingService.logDebug/logError` is called with user text length only today" | ❌ **WRONG.** Verified: `lib/core/services/booking_service.dart:97` calls `LoggingService.logDebug('   Guest: $guestName ($guestEmail)')` — interpolates raw `guestName` + `guestEmail` directly. If LoggingService writes to any text-format consumer (file, server-side log forward), CR/LF in `guestName` smuggles. Audit LoggingService.logDebug's sink before assuming "low-risk". |
| 2.6 (Origin gate) — bookbed CF | "CF callables get this via Firebase App Check + auth context" | ❌ **WRONG.** Verified across `availability.ts`, `emailVerification.ts`, `passwordReset.ts`, others: all `onCall({...})` configs use `cors: true` only. **ZERO `enforceAppCheck: true`** in the codebase. App Check is NOT enforced on any callable. Auth (`request.auth`) is checked individually per-handler, but App Check (anti-bot, anti-script-runner) is not. **Real security gap — Phase B+ priority to enable App Check on every authenticated callable.** |
| 2.8 (Input validation) | "CF callables use `data: any` by default" — port `zod` strict schemas | ✅ **CONFIRMED.** No `zod` / `joi` / structural validation library present. Handlers destructure `request.data?.field` with defaults; some `parseIsoDate(...)` per-field parsers (e.g. `availability.ts:124`). Worth porting Pydantic-equivalent strict schemas. |
| 2.9 (Upload guard) | "Check `storage.rules` + any client upload handlers" | ✅ **VERIFIED — strong.** `storage.rules` enforces: `request.auth != null`, `request.auth.uid == userId` (or property `owner_id` lookup for property images), `request.resource.size < 10 * 1024 * 1024` (10 MB cap), `request.resource.contentType.matches('image/.*')`. iCal exports locked SF-025 (2026-05-22). All Flutter `putData` call sites (`storage_service.dart`, `firebase_owner_properties_repository.dart`) upload images consistent with the rules. **No port needed.** |
| 2.12 (Rate limiting) — bookbed CF | "verify all callable endpoints have it" | ✅ **VERIFIED but path correction.** Module is at `functions/src/utils/rateLimit.ts` (NOT `lib/rateLimit.ts` as doc said). 18 callable sites import it: `atomicBooking`, `authRateLimit`, `customEmail`, `stripe*`, `bookingAccessToken`, `verifyBookingAccess`, `emailVerification`, `guestCancelBooking`, etc. Coverage is wide but not necessarily 100% — a follow-up audit can confirm "every authenticated callable rate-limited". |
| 2.13 (Firestore CHECK-equivalents) | "review whether `firestore.rules` enforces equivalent constraints to LDS's 10 CHECKs" | ⏸️ **DEFERRED — 441-line file requires dedicated audit.** File exists at the expected path; spot-grep did not return the canonical type-check patterns (`is string` / `is number`) — implies bookbed uses a different invariant style. Schedule a focused read pass rather than including in this verification sweep. |

### Action items surfaced by this verification pass (not in original plan)

1. **Enable Firebase App Check** on every `onCall(...)` in `bookbed/functions/src/` — pass `enforceAppCheck: true` in the options object. Mitigates bot/script-runner abuse on every authenticated callable. **Real gap, no port from LDS needed (LDS has no equivalent surface) — pure bookbed work.**
2. **Audit Flutter `LoggingService.logDebug` sink** — if it forwards to a text-format consumer, port the LDS `_CRLFScrubFilter` pattern. If it only stays in-process / structured-JSON, this is informational only.
3. ~~Update Section 2.3 in this doc~~ — done in this commit (row inline corrected).
4. **Schedule a focused `firestore.rules` audit pass** — 441 lines, not in scope here. The rules-drift CI workflow already gates schema changes; one-time audit of the constraint set is the gap.

---

**Created:** 2026-05-22 — Phase 13.14 of LeadDataScraper roadmap. Sourced from LDS `CLAUDE.md`
+ direct file reads on both BookBed repos. Re-verify spot-checks before porting; both repos
are heavily live and update faster than this doc.
