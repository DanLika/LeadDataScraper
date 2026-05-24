# Codebase Invariants (AI router, discovery, frontend handler, navigation)

## AI Router invariants (`src/core/agentic_router.py`)
- `route_instruction()` attaches a `lead_index` (unique_key + name +
  company_name, up to 200 rows) to the Gemini contents so the model can
  resolve "Audit Alpha Tech" → `seo_audit(unique_key=...)`. Without this
  context the model bails with "data insufficient" for every per-lead
  action prompt.
- `_execute_database_query()` selects `unique_key, name, company_name,
  audit_status, seo_score, lead_source, email, phone, website,
  high_risk_flag, segment` — query-answer prompts can compute "high risk"
  and other categorisations from this set without re-querying the DB.
- The query prompt embeds **definitions** ("high risk" = `high_risk_flag`
  true OR `seo_score < 50` OR `audit_status == 'Failed'`; "healthy" =
  Completed + score ≥ 70 + not high-risk; etc.) so the AI's answers match
  the UI's own filter semantics.
- `/ask` auto-executes `DATABASE_QUERY`, `STATUS_CHECK`, and `GET_INSIGHTS`
  (read-only tasks) and surfaces `result.answer / message /
  formatted-insights / summary` as the chat reply. `task == "UNKNOWN"`
  (small-talk / unmapped) surfaces `plan.raw` (Gemini's free-text reply)
  instead of showing a confusing "Confirm task: UNKNOWN" plan card.
- `/execute` rejects extra fields (`extra='forbid'`). The plan returned by
  `/ask` includes a `reasoning` field; the frontend strips it before POST
  (`handleExecutePlan` builds `{task, params}` only) — without the strip
  every Confirm & Execute click 422s.
- `_get_status_summary()` aggregates audit_status counts into a one-line
  natural-language summary (`"401 leads total — 370 Completed, 30 Failed,
  1 Pending."`) and returns it as both `answer` and `summary`, so /ask
  surfaces it without falling back to `"Query executed."`.
- `_generate_outreach_draft()` returns
  `{draft, subject, lead_name, lead_email, operator_name}`. The prompt
  asks Gemini for a "Subject:" first line; the handler parses it out
  with an **atomic-group regex**
  `^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n` — the previous
  form `^\s*Subject\s*:\s*(.+?)\s*\n+` was O(n²) on whitespace-padded
  model output with no trailing newline (a real ReDoS, fixed in this
  branch). Operator name comes from `OPERATOR_NAME` env, defaulting to
  "Your Name". The frontend modal renders subject + body separately and
  offers an Open-in-Gmail deep-link with both prefilled. Linear bound
  locked in by `tests/test_redos.py::TestSubjectParserReDoSRegression`.

## Discovery engine invariants (`src/scrapers/discovery_engine.py`)
- `find_leads(query, location)` is the Google-Maps scrape path. The URL host
  is hardcoded to `google.com` and `query` is `quote_plus`-encoded, so
  there's no host-controlled SSRF surface. The Playwright route guard
  (`_install_ssrf_route_guard`) re-runs `assert_safe_url` on every
  subresource and redirect — closes the TOCTOU gap between pre-flight DNS
  check and `page.goto()`, and blocks any redirect chain hopping to an
  internal host.
- `unique_key` is preferentially derived from the `!1s<id>!` segment of the
  Google-Maps place URL (stable across runs). Falls back to a 16-char MD5
  of `name` when no place-URL is present — `usedforsecurity=False`
  documents the non-crypto intent and keeps Bandit/Semgrep MD5 lints quiet.
  Collisions only route two distinct businesses to the same row; the human
  review queue catches that.
- `_extract_lead_data` returns `{name, unique_key, website, phone, rating,
  audit_status, lead_source: 'google_maps', address}`. Address comes from
  `_extract_address(page, container)` which queries the Maps side-panel
  in this order: `button[data-item-id='address']` → `button[aria-label^=
  'Address:']` → `[data-tooltip='Copy address']`. If the panel isn't
  open, the result card is clicked to open it. Output is normalised via
  `re.sub(r'\s+', ' ', ...)` + `re.search(r'[\w].*')` to drop the leading
  icon glyph + collapse whitespace; returns `None` on miss (never raises).

## Next 16 prerender + `useSearchParams` contract
- `frontend/app/page.tsx` is `'use client'` and uses `useSearchParams()` to
  consume the cross-page nav query params (`?openSettings=1`,
  `?view=audited`, etc.). Next 16 requires every `useSearchParams()`
  consumer to be wrapped in `<Suspense>` so that `next build` can prerender
  the page shell without bailing out to CSR. The default export is a thin
  `<Suspense fallback={null}><DashboardInner /></Suspense>` wrapper; the
  real component is `DashboardInner`. Removing the Suspense will cause
  `next build` to fail with `missing-suspense-with-csr-bailout` at the
  static-generation step — a hard deploy blocker on Render's
  `npm run build` step.
- Local dev `uvicorn` ships the `server: uvicorn` header by default;
  Dockerfile's CMD adds `--no-server-header`. This is cosmetic only and
  prod (via Docker) suppresses the header. The Next.js proxy also strips
  any `server` header on forward as belt-and-braces.

## End-to-end smoke flow (verified 2026-05-21)
Logged-in user → AI chat → natural-language action → Confirm & Execute →
Playwright crawl → Supabase upsert is the load-bearing pipeline. Verified
end-to-end via chrome-devtools MCP against a throw-away Supabase Auth user
on 2026-05-21:
- `"How many leads are in the database?"` → `STATUS_CHECK` autoexec returns
  `"<N> leads total."` (see `_get_status_summary`).
- `"Find me 3 dentists in Mostar"` → `DISCOVERY_SEARCH` plan card → Confirm
  & Execute → orchestrator job → 8 leads in ~35s.
- Cookie floor + Origin gate + X-API-Key proxy injection all hold under the
  full flow. No exceptions in backend log. Re-run via the same MCP browser
  path if the auth / proxy / orchestrator wiring changes.

## Cross-page navigation contract (`frontend/app/page.tsx` useEffect on mount)
- Sidebar/Insights/Campaigns all share the same `<Sidebar>` component, but
  the dashboard owns the state for modals (`showSettings`,
  `showDiscoveryModal`) and view filter (`view`, `searchTerm`). When the
  user clicks Settings/Deep Discovery/Audited/High Risk/a prospect from
  Insights or Campaigns, those pages can't toggle that state directly.
  Instead they navigate to `/` with query params and the dashboard
  consumes-then-strips them:
  - `/?openSettings=1` → opens Settings modal
  - `/?openDiscovery=1` → opens Discovery modal
  - `/?view=audited|high-risk` → toggles the view-filter
  - `/?search=<term>` → bridge-only; translated to `?q=` on consume so
    the filter-state sync (below) sees a consistent vocabulary
- After consuming, the bridge does `router.replace('/?q=<term>')` if
  search was set, else `'/'`. Setters passed to Sidebar on
  non-dashboard pages must respect the `(open)` argument: `(open) => {
  if (open) router.push('/?openSettings=1') }` — otherwise Sidebar's
  `setShowDiscoveryModal(false)` (called when the user clicks Settings)
  navigates to `/?openDiscovery=1` and the wrong modal opens.

## E2E test suite, filter URL state, offline queue, drag-drop, cross-tab
See `docs/e2e-and-frontend-contracts.md` for the full surface added in
the recent test-build session — filter ↔ URL vocabulary
(`?segment/?status/?min/?q/?sort`), `apiFetch` 401 + offline-queue
behaviour, `GET /orchestrator/active`, drag-drop ingest, the 18 E2E
spec files + their projects (chromium/firefox/webkit/iphone-14/pixel-7)
+ required env, the cooperative-cancel pytest, and the ops scripts
(schema-migration-smoke, auth-smoke, contract-smoke, preview-smoke,
data-integrity-cron). Fold sections into this file as they stabilize.

## Frontend handler robustness pattern
Every state-changing handler that hits `/api/proxy/*` MUST:
1. Check `res.ok`; on failure surface
   `data.detail || data.error || \`<Action> failed (HTTP ${status})\`` via
   `showToast(..., 'error')` rather than continuing to update local state.
2. Wrap fetch in try/catch and on network failure show
   `'<Action> failed — backend unreachable.'` toast.
3. Show `aria-busy` + `disabled` on the trigger button during the in-flight
   request and reset in `finally`. Without this, rapid clicks fire
   duplicate jobs and Gemini calls (cost real money).
4. For destructive operations (`processAll`, `startMassivePipeline`,
   `handleDeepHuntAll`, `handleClearLeads`), gate with `confirm()` that
   names the count + a one-line cost warning.

Pydantic 422 responses come as
`{detail: [{type, loc, msg, input, ctx}]}` — `AIChat.handleSubmit` joins
`detail[].msg` so the user sees "String should have at most 4000
characters" instead of a generic placeholder.

