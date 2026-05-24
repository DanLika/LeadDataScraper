# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
- `backend/main.py` — FastAPI app with all API endpoints (leads, campaigns, orchestrator, AI chat, exports). Lazy module-level singletons (`db`, `router`, `auditor`, `orchestrator`) via module `__getattr__`. **PEP 562 caveat:** lifespan explicitly attribute-accesses each name via `sys.modules[__name__]` to populate `globals()`, since `__getattr__` does NOT fire for bare-name LOAD_GLOBAL inside same-module functions.
- `src/utils/supabase_helper.py` — Supabase client wrapper (`SUPABASE_SERVICE_ROLE_KEY`). Hot-path reads `asyncio.to_thread`-wrapped.
- `src/utils/stats_cache.py` — In-process TTL cache (60s) with `asyncio.Lock` stampede guard. Per-uvicorn-worker singleton.
- `src/utils/query_profiler.py` — Dev-only Supabase query profiler, env-gated (`QUERY_PROFILER=1`).
- `src/scrapers/seo_audit.py` — Async SEO auditor (aiohttp).
- `src/scrapers/discovery_engine.py` — Google Maps lead discovery via Playwright.
- `src/scrapers/enrichment_engine.py` — Shared-browser-pool enrichment. `aclose()` MUST be called on teardown.
- `src/core/task_orchestrator.py` — Background job orchestration.
- `src/core/agentic_router.py` — AI instruction routing.

## Layered architecture (PR #192)

  backend/main.py             routing + auth + rate-limit + Pydantic + HTTP error mapping
  src/services/<domain>.py    business logic (typed primitives, NOT Pydantic instances); raises typed domain errors
  src/repositories/<domain>.py  pure PostgREST I/O; translates upstream errors (PGRST205 → CampaignTableMissingError)

First migrated: campaigns. Apply to leads / orchestration next.

## Canonical error hierarchy (`src/errors.py` — PR #195)

```
DomainError
├── NotFoundError                → 404 (Campaign/Lead/NoMatching/NoCampaignMessages)
├── ValidationError              → 400/422
├── ConfigurationError           → 503 (CampaignTableMissingError)
├── LeadError                    → 500 (LeadProcessingError)
├── EnrichmentError              → 500 (Timeout/Extraction)
└── AuditError                   → 500 (Timeout/Fetch)
```

Rules: most-specific class always. NEVER `raise Exception(...)`. `except Exception` ONLY at outermost boundary. Messages for handler authors, not end users — never echo `str(exc)` to client. `src/services/exceptions.py` is a back-compat re-export shim. Inside an `except` use `logger.exception(msg, *args)` (NOT `logger.error(..., exc_info=True)`).

## Constants modules (PR #194)
- `src/utils/constants.py` (backend) + `frontend/app/lib/constants.ts` (frontend)
- Parity invariant: `MAX_UPLOAD_BYTES` (Py) = `MAX_PROXY_BODY_BYTES` (TS). Both carry `BACKEND PARITY` docstring note.

## Quality ratchet (PR #196)
`.github/workflows/quality-ratchet.yml`: ruff(90)/mypy-strict(401)/pylint(10.00)/eslint(0)/semgrep(0). Lower-is-better may roll baseline forward in the same PR; NEVER raise baseline to silence a finding — fix it. Comparator: `scripts/check-quality-baselines.py` (argv lists, no shell).

## Test organization (PR #199)

```
tests/{unit,integration,e2e,security,quality}/
```

Markers (cross-cutting): `slow`, `live`, `security`, `integration`, `e2e`. CI default: `-m "not slow and not live"`. Directory + marker BOTH required; marker drives CLI filter.

Test source-path pattern: use `Path(__file__).resolve().parents[N] / 'src' / ...` (depth-independent), NOT `'..'`-chain.

## Critical pinned findings (do NOT lose on refactors)

1. `seo_score` is NOT an input to `calculate_outreach_score`.
2. `segment_lead` is pure regex, not Gemini.
3. `_get_strategic_insights` SELECTs only `name,company_name,audit_status,seo_score,lead_source` (+ separate `count="exact"` for DB-wide total grounding).
4. `discovery_search` / `run_massive_pipeline` schemas don't declare `limit`.
5. `verify_api_key` returns 403, not 401.
6. Discovery + SEO audit are NOT Gemini calls — excluded from cost budget.

## Known pre-existing test failure
`tests/unit/test_logging_config.py::test_setup_logging` fails on origin/main (test-ordering issue, earlier test resets root logger). Not caused by any session refactor.

## Documentation map (extracted invariant docs)

Detailed invariants extracted from this file to keep it under context-load thresholds. Re-read the relevant doc BEFORE editing any code path it covers:

- **`docs/security-invariants.md`** — API auth, RLS, CSRF/Origin, CSP/headers, SSRF guard, CSV/SMTP/CRLF/log-line/regex-input guards, prompt-injection fence, RLS deny-all + grants matrix, schema drift gate, CHECK constraints, JSONB shape gate, NULL audit, orphan/zombie sweep, concurrency tests, statement timeouts, connection pool, DB bloat, slow queries, function safety, GDPR Articles 17 + 20.
- **`docs/security-test-inventory.md`** — every defense above mapped to its test file (offline / frontend node / opt-in e2e). Update both when defenses change.
- **`docs/perf-invariants.md`** — cursor pagination, async DB wrappers, stats cache, cold-start lazy imports (PEP 562 trap), block-logger middleware, web-vitals RUM, streaming exports, query profiler, browser pool, load-test scaffolding, structured JSON logging, request-context middleware, Sentry, Discord alerting matrix.
- **`docs/codebase-invariants.md`** — AI router behavior, discovery engine contract, Next 16 prerender + `useSearchParams` contract, e2e smoke flow, cross-page nav contract, E2E test suite pointers, frontend handler robustness pattern.
- **`docs/frontend-architecture.md`** — page composition, component split, design tokens, accessibility, BookBed cross-repo strategy.
- **`docs/perf-test-snapshots.md`** — Phase 9 live perf-test reports (2026-05-22 point-in-time).
- **`docs/sessions/`** — historical PR-period narratives. `2026-05-22-patterns.md` (layered arch / errors / constants / ratchet / test reorg), `2026-05-23-drain.md` (#235-#251), `2026-05-23-phase15.md` (audit + 6 fix PRs), `2026-05-23-crossover-gaps.md` (#227/#231/#237).

Operator-facing docs (these existed before extraction):
- `docs/runbooks/operator-guide.md` — day-to-day ops + API reference
- `docs/runbooks/incidents.md` — SEV-1/2 playbooks
- `docs/runbooks/rollback.md` — Render / git revert paths
- `docs/onboarding.md`, `docs/observability.md`, `docs/alerting.md`, `docs/launch-checklist.md`, `docs/support-process.md`, `docs/faq.md`, `docs/status-page-setup.md`, `docs/roadmap.md`, `docs/legal/{privacy-policy,terms}.md`, `docs/adr/{001..007}.md`, `docs/secret-inventory.md`, `docs/ci-architecture.md`, `docs/bookbed-crossover.md`, `docs/e2e-and-frontend-contracts.md`, `docs/tech-debt-register.md`, `docs/architecture/module-graph.md`.

## Quality reports — weekly Monday cadence

Run + update each Monday:
- `tests/quality/dead-code-report.md` (vulture/deptry/ts-prune/knip/depcheck)
- `tests/quality/complexity-report.md` (radon + sonarjs)
- `tests/quality/type-coverage-progress.md` (mypy --strict; target 95% on utils/scrapers/processors)
- `tests/quality/duplication-report.md` (jscpd + pylint)
- `tests/quality/long-functions-report.md` (Python ast > 80 LOC + eslint)
- `tests/quality/component-size-audit.md`, `exception-audit.md`, `docstring-coverage.md` (interrogate, 80% then ratchet), `test-reorg-report.md`

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |

## Session log

Recent session narratives moved to `docs/sessions/`. Latest:
- 2026-05-23 PR drain (#235-#251) — `docs/sessions/2026-05-23-drain.md`
- 2026-05-23 Phase 15 audit + 6 fix PRs — `docs/sessions/2026-05-23-phase15.md`
- 2026-05-23 crossover gaps (#227/#231/#237 + docs-PR stack recipe) — `docs/sessions/2026-05-23-crossover-gaps.md`
- 2026-05-22 layered arch + errors + constants + ratchet + tests reorg — `docs/sessions/2026-05-22-patterns.md`
