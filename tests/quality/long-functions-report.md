# Long-Functions Report

Sweep date: **2026-05-22**
Threshold: **> 80 LOC** per function (function definition through closing
line, blank lines + comments counted — matches eslint's
`max-lines-per-function` default).
Tools:
- Python — `ast.walk(...)` over `src/` + `backend/`
- TypeScript — `eslint --rule '{"max-lines-per-function": ["error", {"max":80,"skipBlankLines":false,"skipComments":false}]}'` over
  `frontend/app/**` + `frontend/utils/**` + `frontend/components/**`

## Headline

| Metric | Value |
|---|---:|
| Python functions > 80 LOC | **11** |
| TypeScript functions > 80 LOC | **12** |
| Total | **23** |
| Biggest | `frontend/app/page.tsx:114 DashboardInner` — **1 624 LOC** |
| Biggest Python | `src/utils/csv_helper.py:71 load_csv_with_unique_key` — **105 LOC** |
| **Refactored this PR** | `load_csv_with_unique_key` 105 → 15 (-86%) |

---

## Python — 11 functions > 80 LOC (ranked)

| # | LOC | File:Line | Function | Single-responsibility verdict |
|---|---:|---|---|---|
| 1 | **105** | `src/utils/csv_helper.py:71` | `load_csv_with_unique_key` | ❌ 4 concerns: read+recovery, column canonicalisation, ensure essentials, generate `unique_key`. **✅ Refactored in this PR → 15-line orchestrator + 4 helpers** |
| 2 | 105 | `src/scrapers/discovery_engine.py:95` | `_extract_lead_data` | ❌ Per-field DOM extraction with multiple selector fallbacks per attribute (name / website / phone / rating / address). Extract `_extract_name`, `_extract_website`, `_extract_phone`, `_extract_rating`, `_extract_reviews` — `_extract_address` already exists as the model. |
| 3 | 103 | `src/core/parallel_auditor.py:286` | `orchestrate_scaling` | ❌ Concurrency-knob decision tree (CPU / memory / queue depth / error rate). Extract a `ScalingPolicy` value object; each input becomes a `_check_<X>(metrics) -> int` returning a candidate concurrency, then `min(candidates)` picks the most conservative. |
| 4 | 102 | `src/scrapers/seo_audit.py:176` | `perform_seo_audit_async` | ✅ It IS a single orchestrator — calls `_check_meta_tags`, `_analyze_headings`, `_detect_tracking_and_tech`, `_detect_portals_and_socials`, `_extract_emails_and_text`. Length is a function of the audit surface, not branching. **Borderline keep**; only refactor if a new audit category lands. |
| 5 | 102 | `src/core/parallel_auditor.py:173` | `audit_single_lead` | ❌ Per-lead dispatch + status writes + error-path branches. Extract `_persist_audit_result`, `_persist_audit_error`. |
| 6 | 102 | `src/core/agentic_router.py:37` | `_get_tools` | ✅ Pure data (Gemini function-call schemas list). Splitting per-tool helps readability but adds ceremony. **Keep** unless a new tool needs review; preferably split into `_TOOL_SEO_AUDIT = {...}` module-level constants and have `_get_tools` return `[_TOOL_SEO_AUDIT, _TOOL_DISCOVERY, ...]`. |
| 7 | 95 | `src/scripts/export_leads.py:43` | `export_leads` | ❌ CLI script doing argparse + DB query + 3 export-format branches + reporting. Extract `_export_full`, `_export_outreach`, `_export_facebook` (the `csv_helper` module already has the formatters; the script just dispatches). |
| 8 | 85 | `backend/main.py:960` | `generate_campaign_messages` | ❌ **Service-layer candidate** per user's hint. Inner `async def generate_messages()` closes over the outer frame to build per-channel email/linkedin bodies. Extract: `_fetch_campaign(id) -> (camp, err)`, `_select_leads_for_campaign(camp) -> list[dict]`, `_build_email_message(...)`, `_build_linkedin_message(...)`. Move to `src/services/campaign_service.py`. Handler shrinks to ~25 LOC. |
| 9 | 84 | `src/core/agentic_router.py:366` | `_generate_outreach_draft` | ⚠️ Has long prompt-template string (~30 LOC) inside the function body. Lift the prompt to a module-level f-string constant; the remaining logic (Gemini call + subject regex parse + signature splice) is ~50 LOC. **Subject regex is locked in by `test_redos.py::TestSubjectParserReDoSRegression`** — preserve verbatim. |
| 10 | 83 | `src/processors/ai_mapper.py:39` | `get_column_mapping` | ❌ Gemini call + prompt construction + JSON-fence response parse + fallback heuristics. Extract `_build_mapping_prompt(...)`, `_parse_mapping_response(...)`, `_fallback_heuristic_mapping(...)`. Live golden tests in `tests/test_ai_mapper_golden.py` cover 15 header variants — refactor is safe. |
| 11 | 82 | `src/core/task_orchestrator.py:223` | `_process_in_chunks` | ⚠️ Already has tight `finally:` lifecycle (browser pool tear-down + stats_cache invalidation per CLAUDE.md). Decomposing risks accidentally moving the `aclose()` call out of `finally`. Extract carefully: `_process_chunk(chunk) -> ChunkResult` for the per-chunk body; keep the loop + `finally` as-is. |

---

## TypeScript — 12 functions > 80 LOC (ranked)

| # | LOC | File:Line | Function | Single-responsibility verdict |
|---|---:|---|---|---|
| 1 | **1 624** | `frontend/app/page.tsx:114` | `DashboardInner` | ❌❌❌ The headline target user called out. One component owns: filter state (segment / status / score / search / sort), URL sync, cross-page bridge consume-and-strip, cursor pagination, modal state (settings / discovery), lead-mutation handlers, offline queue wiring. **Test-first multi-PR effort**: extract `useLeadFilters()` custom hook → extract `<ModalSlot>` for settings + discovery → extract `useCursorPaginatedLeads()` → extract `<DashboardHeader>` + `<DashboardActions>`. Expected per-step delta: -200 to -400 LOC. |
| 2 | 512 | `frontend/app/campaigns/page.tsx:42` | `CampaignsPage` | ❌ Single component for list view + creation flow + edit + send action. Extract `<CampaignList>`, `<CampaignForm>`, `<CampaignSendButton>` (each a child component with its own state). |
| 3 | 369 | `frontend/app/components/AIChat.tsx:25` | `AIChat` (default export) | ❌ Combines floating-chat layout + message history + form + plan-card rendering + execute-plan diff. Extract `<ChatMessageList>`, `<ChatComposer>`, `<PlanCard>` (latter is the Confirm & Execute UI). |
| 4 | 348 | `frontend/app/components/LeadTable.tsx:120` | `LeadTable` | ❌ Virtualizer setup + sticky header + row renderer + measureElement + Load More button + auxiliary `last_error` / `key_offerings` / `pain_points` panel. Extract `<LeadRow>`, `<LeadDetailPanel>`. The virtualizer state stays in the parent. |
| 5 | 320 | `frontend/app/components/Sidebar.tsx:43` | `Sidebar` | ❌ Render mixes nav structure + mobile drawer transform + setter-shim for cross-page modals + insights widget. Extract `<NavList>`, `<MobileDrawer>`, `<InsightsWidget>` (the widget is its own concern — it fetches insights only when expanded). |
| 6 | 254 | `frontend/app/insights/page.tsx:36` | `InsightsPage` | ❌ Fetches insights + renders 4 chart panels + AI strategic analysis section. Charts already lazy-load via `<InsightsCharts>` — extract the strategic analysis block into `<StrategicAnalysisPanel>`. |
| 7 | 211 | `frontend/app/components/LeadTable.tsx:234` | (inner LeadTable callback / second function in the file) | ❌ Subsumed by the LeadTable extraction in #4. |
| 8 | 132 | `frontend/app/api/proxy/[...path]/route.ts:75` | `forward()` | ⚠️ Per-method branch + Origin gate + auth-revalidate + path-special-cases (`leads/clear`, `metrics`) + scheme assertion. Each branch is small but they add up. Extract: `_resolveOrigin(req)`, `_buildUpstreamHeaders(...)`. Keep the auth-revalidate inline (it's the core security gate). |
| 9 | 107 | `frontend/app/components/AIChat.tsx:194` | (inner AIChat callback) | ❌ Subsumed by AIChat extraction in #3. |
| 10 | 104 | `frontend/app/components/FilterBar.tsx:31` | `FilterBar` | ⚠️ Acceptable — filter UI legitimately has many controls (segment / status / score range / search). Extract per-control sub-components only if `<FilterBar>` grows further. |
| 11 | 93 | `frontend/utils/supabase/middleware.ts:12` | `updateSession` | ⚠️ Supabase SSR cookie-floor + auth gate + public-path allowlist. The cookie-floor logic is security-critical and already locked in by `cookie-floor.test.mjs` + `cookie-floor-fuzz.test.mjs`. Refactor only if a test break it would surface. **Keep**. |
| 12 | 83 | `frontend/app/login/page.tsx:42` | `LoginPage` | ⚠️ Form + state machine + error display. Borderline; pairs with the duplication-report item C (`<LabeledInput>` extraction) — when that lands, this drops below 80. **Wait for duplication-report C**. |

---

## What this PR ships

`src/utils/csv_helper.py::load_csv_with_unique_key` decomposed into a
thin orchestrator + 4 single-concern helpers + 2 module-level constants
for the previously inline maps.

| Helper | LOC | Concern |
|---|---:|---|
| `_read_csv_with_recovery(filepath, df_name, essential_cols)` | 30 | bad-row-tolerant load with the FileNotFound / EmptyData / ParserError fallback chain (the "lose-data silent" bug fix from BUGS.md Round 4 B is preserved as a top-of-helper comment) |
| `_canonicalize_columns(df)` | 28 | rename source columns to canonical names; rename-not-copy invariant preserved verbatim (prevents the silent data-lossy pandas `to_dict('records')` drop) |
| `_ensure_essential_columns(df, essential_cols)` | 6 | add Name / Website / email / unique_key if missing |
| `_ensure_unique_key(df, df_name)` | 45 | UNIQUE_KEY → unique_key sync + per-row fallback generation `(w_e | w | n | idx_<row.name>)` |
| **`load_csv_with_unique_key`** (orchestrator) | **15** | 4-step pipeline |

**LOC delta: 105 → 15** in the orchestrator (-86%); each helper is under
the 80-LOC threshold. mypy --strict still passes. The existing 40 tests
across `tests/test_csv_helper_health.py` + `tests/test_security_helpers.py`
all pass unchanged.

## What did NOT change

- The rename-vs-copy logic comment block (the "BUGS.md Round 4 B"
  history note) is preserved verbatim in the new `_canonicalize_columns`
  docstring.
- Nested `get_val` + `generate_row_key` closures stay nested inside
  `_ensure_unique_key` — they're only used there, and moving them to
  the module level would expose pandas Series internals to other
  callers that have no use for them.
- No new env vars, no new dependencies, no test additions or deletions.

---

## Roadmap (deferred PRs, one per offender)

Order chosen by leverage-per-risk: tested-first, then small-blast-radius:

| Order | Target | Strategy summary |
|---|---|---|
| 1 | `_generate_outreach_draft` | Lift prompt template; preserve subject regex (locked by ReDoS test) |
| 2 | `get_column_mapping` | Extract `_build_mapping_prompt` + `_parse_mapping_response`; covered by `test_ai_mapper_golden.py` |
| 3 | `_get_tools` | Lift each tool schema to module constant (mechanical) |
| 4 | `export_leads` | Extract per-format dispatch; CLI surface stays the same |
| 5 | `_extract_lead_data` (discovery) | Per-field extractors with `_extract_address` as the model |
| 6 | `audit_single_lead` (parallel auditor) | Extract `_persist_audit_result` + `_persist_audit_error` |
| 7 | `orchestrate_scaling` | `ScalingPolicy` value object + per-input `_check_<X>` |
| 8 | `_process_in_chunks` | Extract `_process_chunk`; keep loop + `finally` |
| 9 | `generate_campaign_messages` (backend handler) | Service-layer extraction to `src/services/campaign_service.py` |
| 10 | `perform_seo_audit_async` | Borderline — defer unless audit surface grows |
| 11 | `<NavShell>` extraction (frontend) | Pairs with duplication-report item D (3rd page joins) |
| 12 | `<StatCard>` extraction (frontend) | Duplication-report item B |
| 13 | `<LabeledInput>` extraction (frontend) | Duplication-report item C — also drops `LoginPage` below 80 LOC |
| 14 | `Sidebar` decomposition | `<NavList>` + `<MobileDrawer>` + `<InsightsWidget>` |
| 15 | `LeadTable` decomposition | `<LeadRow>` + `<LeadDetailPanel>` |
| 16 | `AIChat` decomposition | `<ChatMessageList>` + `<ChatComposer>` + `<PlanCard>` |
| 17 | `InsightsPage` decomposition | `<StrategicAnalysisPanel>` |
| 18 | `CampaignsPage` decomposition | `<CampaignList>` + `<CampaignForm>` + `<CampaignSendButton>` |
| 19 | `forward()` (api/proxy) | `_resolveOrigin` + `_buildUpstreamHeaders`; auth-revalidate stays inline |
| 20 | `FilterBar` per-control sub-components | Wait for next FilterBar growth |
| 21 | `updateSession` (supabase middleware) | Defer — security-critical, cookie-floor tests govern |
| 22 | `LoginPage` | Drops out after `<LabeledInput>` extraction (#13) |
| 23 | **`DashboardInner` page.tsx CC 54 / 1 624 LOC** | Test-first multi-PR. Order: `useLeadFilters()` hook → `useCursorPaginatedLeads()` hook → `<ModalSlot>` for settings + discovery → header / actions extraction. Estimated total: 4-6 PRs, each -200 to -400 LOC. |

---

## Reproducing

### Python

```py
import ast
roots = ['/path/to/src', '/path/to/backend']
hits = []
for root in roots:
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith('.py'):
                p = os.path.join(dp, fn)
                tree = ast.parse(open(p).read(), filename=p)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        loc = (node.end_lineno or node.lineno) - node.lineno + 1
                        if loc > 80:
                            hits.append((loc, p, node.lineno, node.name))
hits.sort(reverse=True)
for loc, f, ln, name in hits:
    print(f"{loc:3} LOC  {f}:{ln}  {name}")
```

### TypeScript

Drop this flat config at `frontend/.cx_fnsize.config.mjs`:

```js
import tseslint from "typescript-eslint";
export default [{
  files: ["**/*.{ts,tsx,mts,mjs,js,jsx}"],
  ignores: ["**/node_modules/**","**/.next/**","**/dist/**","**/build/**","**/out/**","next-env.d.ts"],
  languageOptions: { parser: tseslint.parser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
  rules: { "max-lines-per-function": ["error", { max: 80, skipBlankLines: false, skipComments: false, IIFEs: true }] },
}];
```

Then:

```sh
cd frontend && npx --no-install eslint --no-config-lookup \
  --config .cx_fnsize.config.mjs --no-warn-ignored \
  "app/**/*.{ts,tsx}" "utils/**/*.{ts,tsx,mjs}" "components/**/*.{ts,tsx}"
```

(`typescript-eslint` must be installed locally — `--no-save` install
during measurement, do not commit lockfile churn.)

## Re-run cadence

Weekly, alongside `tests/quality/*.md`. Track deltas here:

| Week of | Python > 80 | TS > 80 | Top mover | PRs landed |
|---|---:|---:|---|---|
| 2026-05-22 | 11 → **10** | 12 | `load_csv_with_unique_key` 105 → 15 | this PR |
