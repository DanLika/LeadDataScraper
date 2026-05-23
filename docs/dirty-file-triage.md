# Dirty File Triage — Phase 12.14

**Generated:** 2026-05-22
**Working tree on:** `fix/csp-nonce-rsc-hydration` (misnamed — see `git-state-2026-05.md`)
**Totals:** 30 modified tracked files + 183 untracked = **213 paths**
**Net diff:** 30 files changed, **+6766** / **−871** lines

## Headline

**Zero accidental files. Zero revert candidates.** All 213 paths are work-in-progress that maps to known phases recorded in `CLAUDE.md`. The decision is *how to package and land*, not whether to keep.

A small subset (~10 files) is gitignore-candidate (generated traces, tool configs).

**One bucket is a Sev-1 production fix sitting uncommitted — see Bucket S1, land it first.**

---

## Bucket S1 — CSP nonce / RSC hydration fix (Sev-1, HIGHEST PRIORITY)

**Status:** Modified files, uncommitted. This is the namesake of the current branch.

`docs/findings/2026-05-22-csp-blocks-prod-hydration.md` documents a **Sev-1**: static `script-src 'self'` in `next.config.ts` blocked React hydration in every production build — login impossible, every authed page bricked. The finding doc marks it **RESOLVED** with an end-to-end chrome-devtools-mcp verification (15 streamed scripts carry the per-request nonce, login renders, hydration succeeds).

**The fix is fully implemented but NOT committed.** It spans 4 modified files:

| File | CSP-fix role |
|---|---|
| `frontend/proxy.ts` | Generates 16-byte base64 nonce per request; sets `x-nonce` request header + matching `Content-Security-Policy` response header |
| `frontend/utils/supabase/middleware.ts` | `updateSession` threads the request-header override into `NextResponse.next` |
| `frontend/app/layout.tsx` | `export const dynamic = 'force-dynamic'` + `(await headers()).get('x-nonce')` to register the `headers()` dependency |
| `frontend/next.config.ts` | Static `Content-Security-Policy` line removed (other static headers stay) |

Plus `docs/findings/2026-05-22-csp-blocks-prod-hydration.md` (the finding, untracked).

**Recommended branch:** keep `fix/csp-nonce-rsc-hydration` — the name is *correct* for this work. (It is the other 13 committed commits that are the misfit — see `git-state-2026-05.md`.)

> ### File-sharing conflict — operator decision required
>
> Two of the 4 files are **also touched by other buckets**:
> - `frontend/app/layout.tsx` → also has the WebVitalsReporter mount (Bucket C)
> - `frontend/next.config.ts` → also has Sentry plugin wiring (Bucket D)
>
> A single file can't cleanly split across two PRs. Three options:
>
> | Option | Trade-off |
> |---|---|
> | **A. S1 PR takes the whole files** | Sev-1 hotfix carries unrelated WebVitals + Sentry-build changes. Bad — a hotfix should be minimal. |
> | **B. Land Buckets C + D first, S1 rebases on top** | Clean diffs, but a Sev-1 waits behind 2 feature PRs. Unacceptable if prod is currently broken. |
> | **C. (recommended) Split the 4 files by hunk** — S1 PR commits ONLY the CSP-related hunks of `layout.tsx` + `next.config.ts`; the WebVitals/Sentry hunks stay uncommitted for C/D | Requires `git add -p` hunk staging. Cleanest result, modest effort. |
>
> **First action:** confirm whether production is *currently* broken. If a prod deploy went out with the static `script-src 'self'`, this is an active outage → Option C now. If prod hasn't deployed since the bug, Option B is acceptable.

**Land order:** #0 — before everything, unless Option B is chosen.

---

## Bucket A — CI/CD infrastructure (Phase 11, ~37 files)

**Status:** All untracked. Inventory documented in `CLAUDE.md` § CI/CD architecture + `docs/ci-architecture.md` (also untracked).

| Path | Count |
|---|---|
| `.github/workflows/*.yml` | 19 (new) |
| `.github/scripts/*.{py,mjs}` | 8 |
| `.github/actions/discord-notify/action.yml` | 1 |
| `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/release-drafter.yml`, `.github/workflow-hashes.json` | 4 |
| `.pre-commit-config.yaml`, `Makefile` | 2 |
| `.github/workflows/security.yml` | 1 *(modified, not new)* |

**Recommended branch:** `feature/phase-11-ci-infrastructure`
**Land order:** Mid-pack — needs to follow PR queue (#196 quality-ratchet baseline conflicts otherwise) but precede the test buckets that depend on the CI runners.

**Action:** Split-decision optional. Workflows + scripts + actions are tightly coupled — single PR is correct.

---

## Bucket B — Documentation (Phase 11, ~25 files)

**Status:** Untracked.

| Path | Count | Type |
|---|---|---|
| `docs/adr/*` | 8 | Architecture Decision Records |
| `docs/runbooks/*` | 3 | On-call procedures |
| `docs/legal/*` | 2 | Privacy + terms |
| `docs/findings/*` | 1 | CSP-blocks-prod-hydration |
| `docs/{alerting,ci-architecture,e2e-and-frontend-contracts,faq,font-audit,launch-checklist,observability,onboarding,post-deploy-smoke,roadmap,secret-inventory,status-page-setup,support-process,synthetic-monitor}.md` | 14 | Operator + ops docs |
| `PROJECT_REPORT.md`, `README.md` | 2 | Root |

**Recommended branch:** `docs/phase-11-runbooks-adrs` — pure-doc PR, low review-effort, can land late in queue.

**Note:** Three files in this triage output (`pr-merge-plan.md`, `git-state-2026-05.md`, `dirty-file-triage.md`) belong here once written. Add to the same branch.

---

## Bucket C — Frontend components extracted (Phase 10, ~10 files)

**Status:** Mix of modified + untracked. Documented in `CLAUDE.md` § Frontend Architecture.

**New components:**
- `frontend/app/components/InsightsCharts.tsx` — extracted from `/insights/page.tsx` for lazy-load
- `frontend/app/components/LeadTable.tsx` — virtualized lead inventory (`@tanstack/react-virtual`)
- `frontend/app/components/OfflineBanner.tsx`
- `frontend/app/components/WebVitalsReporter.tsx` — mounted from `app/layout.tsx`
- `frontend/app/types/lead.ts` — shared `Lead` interface
- `frontend/axe-allowlist.json` — accessibility baseline

**Modified callers:**
- `frontend/app/page.tsx` *(modified — wires new components, cursor pagination)*
- `frontend/app/layout.tsx` *(modified — mounts WebVitalsReporter; **also carries the Bucket S1 CSP `headers()`/`force-dynamic` change** — hunk-split required, see S1)*
- `frontend/app/components/AIChat.tsx` *(modified — error-handling improvements)*
- `frontend/app/components/FilterBar.tsx` *(modified — URL-state sync)*
- `frontend/app/insights/page.tsx` *(modified — lazy InsightsCharts)*
- `frontend/app/campaigns/page.tsx` *(modified — lazy AIChat)*

**Recommended branch:** `frontend/phase-10-component-extraction-virtualization`

**Note:** Tight coupling — can't separate components from their callers without leaving the modified callers stranded. Keep as one PR.

---

## Bucket D — Frontend instrumentation + Sentry (Phase 11, 4 files)

**Status:** Untracked.

- `frontend/instrumentation.ts`, `frontend/instrumentation-client.ts`
- `frontend/sentry.edge.config.ts`, `frontend/sentry.server.config.ts`
- *Implied modified:* `frontend/next.config.ts` *(Sentry plugin wiring; **also carries the Bucket S1 static-CSP removal** — hunk-split required, see S1)*
- *Implied modified:* `frontend/package.json`, `frontend/package-lock.json` *(Sentry SDK)*
- *Implied modified:* `frontend/tsconfig.json` *(may include new paths)*

**Recommended branch:** `frontend/sentry-instrumentation`

**Pre-merge:** Per `CLAUDE.md` § Sentry tagging contract, ensure `request_id` + `user.email` tags fire on at least one tested error path. Smoke-test with `next dev` + force-thrown.

---

## Bucket E — E2E test suite (Phase 11, 18 files)

**Status:** Untracked.

- `frontend/e2e/*.spec.ts` × 17: `a11y, aichat, auth, csv-drag-drop, csv-upload, exports, filter-sort, full-flow, locale, memory-soak, mobile, modals, multi-tab, navigation, network-resilience, polling, security-headers, visual`
- `frontend/e2e/tsconfig.json`
- `frontend/playwright.config.ts`

**Recommended branch:** `frontend/e2e-playwright-suite`

**Land AFTER:** Bucket A (CI workflows referenced — `e2e.yml` workflow expects this layout) and Bucket C (component IDs the specs assert on).

---

## Bucket F — Backend DB safety gates (Phase 11, 16 scripts)

**Status:** All untracked. Each maps to a CI job + `CLAUDE.md` invariant.

| Script | Invariant from CLAUDE.md |
|---|---|
| `check_analyze_freshness.py` | ANALYZE freshness gate |
| `check_db_bloat.py` | DB bloat report |
| `check_function_safety.py` | Function safety audit |
| `check_grants_matrix.py` | Grants matrix audit |
| `check_jsonb_shapes.py` | JSONB shape gate |
| `check_null_audit.py` | NULL ratio audit |
| `check_orphans_and_zombies.py` | Orphan + zombie sweep |
| `check_query_plans.py` | Hot-path index gate |
| `check_referential_integrity.py` | Referential integrity gate |
| `check_statement_timeouts.py` | Per-role statement_timeout |
| `cost_report.py` | (per CLAUDE.md ops) |
| `purge_expired_audit_log.py` | GDPR cron (Phase 11.9) |
| `schema_drift_check.py` | Schema + RLS drift gate — **needs Phase 12.9 fix, see below** |
| `slow_query_report.py` | Slow query report |
| `storage_report.py` | Storage size + WoW growth |
| `suggest_jsonb_indexes.py` | JSONB GIN suggestions |

**Recommended branch:** `backend/phase-11-db-safety-gates`

**Couples to:** `supabase_schema.sql` *(modified)* — confirmed to contain `account_deletions` (CREATE TABLE + index + RLS enable + `account_deletions_deny_all` policy, lines 294–310). Schema drift gate will fail any PR that lands the scripts WITHOUT the matching schema.

> ### Phase 12.9 is REAL and lands HERE — not a forward reference
>
> `schema_drift_check.py` currently registers only 4 tables. Its constant is named **`TABLES`** (line 38) — *not* `EXPECTED_TABLES` as the Phase 12 prompt states. Current value:
> ```python
> TABLES: tuple[str, ...] = ("leads", "campaigns", "campaign_messages", "orchestration_jobs")
> ```
> The working-tree `supabase_schema.sql` already has a 5th table (`account_deletions`). The moment Bucket K's schema change lands, the drift CI gate goes **RED** ("extra table in DB, undeclared in checker") unless `schema_drift_check.py` is fixed **in the same PR**.
>
> **12.9 fix (apply in this bucket's branch):**
> 1. Add `"account_deletions"` to the `TABLES` tuple.
> 2. Add `account_deletions` to the RLS deny-all check list (docstring lines 15–18 say "4 tables" — becomes 5).
> 3. Add it to the no-anon/authenticated/PUBLIC GRANT check list.
> 4. `TABLE_CONSTRAINT_KEYWORDS` — **no entry needed**: `account_deletions` has no CHECK constraints in the schema (only table/index/RLS/policy).
> 5. Update `CLAUDE.md` RLS section ("4 tables" → "5 tables").

**Land BEFORE:** Bucket A's workflow PR (workflows reference these scripts).
**Land AFTER:** matching schema migration applied to live Supabase project (per `CLAUDE.md` policy — schema first, gate second).

---

## Bucket G — Backend utility additions (Phase 9-10, 2 files)

**Status:** Untracked.

- `src/utils/query_profiler.py` — env-gated profiler (`QUERY_PROFILER=1`)
- `src/utils/stats_cache.py` — 60s TTL cache for `/stats`

**Modified callers:** `backend/main.py`, `src/utils/supabase_helper.py`, `src/core/task_orchestrator.py` *(invalidate hook)*

**Recommended branch:** `backend/phase-9-10-utils-perf`

**Already documented in:** `CLAUDE.md` § Performance + observability invariants.

---

## Bucket H — Backend tests batch (Phase 9-10-11, ~45 files)

**Status:** All untracked. Each enforces one CLAUDE.md invariant.

| Category | Files |
|---|---|
| Security defenses | `test_agentic_router_behavior.py`, `test_crlf_injection.py`, `test_endpoint_hardening.py`, `test_error_message_leak.py`, `test_idor_sweep.py`, `test_json_pollution.py`, `test_jwt_manipulation.py`, `test_open_redirect.py`, `test_prompt_injection_corpus.py`, `test_proxy_origin_csrf_e2e.py`, `test_pydantic_models_meta.py`, `test_redos.py`, `test_refusal_boundaries.py`, `test_ssrf_deep.py`, `test_ssrf_guard_regression.py`, `test_supabase_anon_bypass.py`, `test_timing_attack.py`, `test_upload_attacks.py` |
| AI quality | `test_ai_cost_budget.py`, `test_ai_mapper_golden.py`, `test_ask_determinism.py`, `test_campaign_diversity.py`, `test_i18n_outreach.py`, `test_insights_quality.py`, `test_json_compliance.py`, `test_linkedin_golden_set.py`, `test_outreach_golden_set.py`, `test_outreach_hallucination.py`, `test_outreach_score_properties.py`, `test_pain_points_consistency.py`, `test_prompt_snapshots.py`, `test_segment_stability.py` |
| Concurrency | `test_concurrent_writes.py`, `test_concurrency_rate_limit_e2e.py`, `test_connection_pool.py` |
| GDPR | `test_gdpr_deletion.py`, `test_gdpr_export.py` |
| Other | `test_logging_request_id.py`, `test_orchestrator_cooperative_cancel.py` |
| Fixtures | `tests/fixtures/prompt_snapshots.json` |
| Contracts | `tests/contracts/{README.md,ask_post.json,health_schema_get.json,leads_get.json,liveness_get.json}` |

**Modified targets:** `backend/main.py`, `src/core/agentic_router.py`, `src/integrations/email_sender.py`, `src/utils/logging_config.py`, etc. *(each test asserts behavior of the modified file)*

**Recommended branch:** `tests/phase-9-10-11-security-ai-concurrency`

**Sub-decision:** Could split GDPR tests into separate PR if the GDPR endpoints PR is also separate. Confirm in `CLAUDE.md` whether GDPR backend code is in another branch already.

---

## Bucket I — Loadtest + chaos scaffolding (Phase 10, 13 files)

**Status:** Untracked. Documented in `CLAUDE.md` § Performance + observability.

- `tests/loadtest/locustfile.py`, `bench_enrich.py`, `spike_locustfile.py`, `drop_supabase_pool.py`
- `tests/loadtest/{soak.sh,spike.sh}`
- `tests/loadtest/{SOAK_PLAYBOOK.md,chaos.md}`
- `tests/loadtest/reports/import_profile_baseline.txt`, `import_profile_lazy.txt`

**Recommended branch:** `tests/phase-10-loadtest-scaffold`

**Note:** `tests/loadtest/reports/*.txt` are generated profile dumps — could be `.gitignore`'d going forward but the baseline files are evidence and small enough to commit once.

---

## Bucket J — Perf traces + reports (Phase 10, 12 files)

**Status:** Untracked. Mostly large JSON traces.

| File | Type | Size sensitivity |
|---|---|---|
| `tests/perf/scroll-analysis.md`, `console-sweep.md`, `long-tasks.md`, `mobile-real-device.md`, `network-waterfall.md` | Markdown reports | Small — commit |
| `tests/perf/dashboard-interaction-trace.json`, `network-cold.json`, `network-warm.json`, `scroll-trace-paced.json`, `scroll-trace-raf.json`, `trace-cold-dashboard.json` | Chrome DevTools trace JSON | **Large — gitignore candidates** |

**Recommended branch:** `tests/phase-10-perf-reports`

**Action:** Add `tests/perf/*.json` to `.gitignore` *(for traces only — keep `tests/perf/*.md` and `tests/loadtest/reports/*.txt` because reproducible reports are useful PR evidence)*.

---

## Bucket K — Backend supabase_schema + ops (Phase 11)

**Status:** Modified.

- `supabase_schema.sql` *(modified — 154 changed lines: adds `account_deletions` table (GDPR, lines 294–310), CHECK constraints, statement_timeout + index migrations)*
- `Dockerfile` *(modified — `build-essential` purge + HEALTHCHECK per CLAUDE.md)*
- `render.yaml` *(modified — `ALLOWED_ORIGINS` + `ADMIN_TOKEN` envVars per CLAUDE.md)*
- `requirements.in` *(untracked — pip-tools input — Phase 11.4)*

**Sequence:** Schema changes apply to Supabase FIRST, then schema-drift CI gate enables, then this PR lands. Per `CLAUDE.md` policy: "schema first, gate second".

> **`requirements.in` / `requirements.txt` are a same-commit pair.** `requirements.in` is untracked (new direct dep — `sentry-sdk[fastapi]` per Phase 12.10). `requirements.txt` MUST be regenerated from it via `make lock-python` (`pip-compile --generate-hashes`). Per `CLAUDE.md`: *"Day-one blocker — operator must run `make lock-python` once locally before the next merge or both `lockfile-sync` CI AND the Docker `--require-hashes` build will fail."* Pre-merge step for this bucket: run `make lock-python`, commit `requirements.in` + regenerated `requirements.txt` together.

---

## Bucket L — Top-level config + meta

- `CLAUDE.md` *(modified — many invariants added)*
- `SECURITY.md` *(modified — security policy)*

**Decision:** Roll into the doc PR (Bucket B) — pure-doc landing.

> **CLAUDE.md has a large uncommitted delta.** `git diff 926721a -- CLAUDE.md` is **1247 lines** — the working-tree CLAUDE.md (the full canonical brief, all invariants) is far ahead of what commit `926721a` (in the 13 ahead-commits) captured. Two consequences:
> 1. PR #200 ("document session 2026-05-22 patterns") edits CLAUDE.md against `origin/main` — its base is ~1247 lines behind the working-tree version. Expect a **substantial CLAUDE.md merge conflict** when Bucket L lands after #200.
> 2. Don't let Bucket L's CLAUDE.md and the Stage-4 ahead-commits' `926721a` double-edit the same sections. Land #200 first, then rebase, then resolve once.

---

## Bucket M — Frontend lockfile + package

- `frontend/package.json` *(modified — likely Sentry + new deps)*
- `frontend/package-lock.json` *(modified — lockfile regen)*
- `frontend/tsconfig.json` *(modified)*

**Land WITH:** the bucket that actually introduces the new dep (most likely Bucket D — Sentry instrumentation).

**Per CLAUDE.md frontend-dep policy:** `next`, `@supabase/ssr`, `@supabase/supabase-js` carry no `^` prefix in `package.json`. Verify the modified `package.json` preserves that.

---

## Gitignore additions recommended

Add to `.gitignore`:

```
# Performance traces (large JSON, regenerated per session)
tests/perf/*.json
!tests/perf/*.json.example

# Loadtest profile dumps (generated)
# (keep tests/loadtest/reports/import_profile_baseline.txt — that's evidence)

# context-mode tool configs (local-only)
frontend/.cx_*.config.mjs
```

**Files to also git rm** *(if found already tracked anywhere):* `frontend/.cx_fnsize.config.mjs`, `frontend/.cx_sonar.config.mjs`.

---

## Land order summary

| # | Bucket | Branch | Depends on |
|---|---|---|---|
| **0** | **S1 — Sev-1 CSP fix** | **`fix/csp-nonce-rsc-hydration`** (keep name) | **— (land first; see S1 file-sharing decision)** |
| 1 | K | `backend/schema-and-ops` | — |
| 2 | F | `backend/phase-11-db-safety-gates` | K |
| 3 | G | `backend/phase-9-10-utils-perf` | — |
| 4 | A | `feature/phase-11-ci-infrastructure` | F (scripts referenced) |
| 5 | C | `frontend/phase-10-component-extraction` | — |
| 6 | D + M | `frontend/sentry-instrumentation` | — |
| 7 | E | `frontend/e2e-playwright-suite` | A, C |
| 8 | H | `tests/phase-9-10-11-suite` | All backend buckets above |
| 9 | I + J | `tests/perf-loadtest-evidence` | A |
| 10 | B + L | `docs/phase-11-runbooks-adrs` | (last, references everything) |

Total: **11 PRs** for the 213-path payload (1 Sev-1 hotfix + 10 feature/doc PRs), sized roughly evenly. Each independently reviewable. None should exceed ~3000 LOC.

---

## Total triage

| Disposition | Count | % |
|---|---|---|
| Branch + commit (work-in-progress) | ~203 | 95% |
| Gitignore candidates | ~7 | 3% |
| Revert candidates | 0 | 0% |
| Accidental | 0 | 0% |
| Generated (rebuilt on demand) | ~10 traces | 5% — keep as evidence first time, gitignore future |
