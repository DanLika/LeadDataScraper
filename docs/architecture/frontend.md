# Frontend architecture + conventions

Sourced from CLAUDE.md 2026-05-29 slim.

## Files
- `app/page.tsx` — Dashboard. Cursor-pagination state + `loadMoreLeads`. Heavy children lazy via `next/dynamic`: `HealthChart` (recharts), `AIChat`, `LeadTable`. `StatsCards` accepts `totalLeads` from `/stats.total_leads` (PR #244 — was showing page-load 50 while DB held 521); falls back to `leads.length` until first /stats. **Outstanding**: PENDING/HIGH-RISK/HEALTHY still derive from loaded slice — needs per-bucket counts in `/stats`.
- `app/insights/page.tsx` — Recharts panels extracted to `InsightsCharts` (lazy). Hits `/leads?limit=200` for aggregation.
- `app/campaigns/page.tsx` — Outreach campaigns.
- `app/components/LeadTable.tsx` — Virtualized. `@tanstack/react-virtual`, CSS-grid rows (not `<table>` — virtualizer needs absolute positioning), sticky header, variable heights via `measureElement`, 20-row overscan. Owns "Load more" + auxiliary panel + `cleanMarkdown` + `CollapsibleText`.
- `app/components/InsightsCharts.tsx` — PieChart + BarChart extracted from `/insights` so recharts (~80 KB gz) loads via lazy chunk.
- `app/components/WebVitalsReporter.tsx` — `useEffect` registers CLS/INP/LCP/FCP/TTFB; `sendBeacon` to `/api/proxy/metrics`. Renders nothing.
- Other components: `AIChat.tsx`, `Sidebar.tsx`, `HealthChart.tsx`, `StatsCards.tsx`, `FilterBar.tsx`, `LocaleSwitcher.tsx`.
- `app/types/lead.ts` — Shared `Lead` interface (imported by `page.tsx` + `LeadTable.tsx` — two identical interfaces in different files break callback variance).
- `app/globals.css` — Design tokens. `--font-main` no longer includes Inter (PR #239).
- `utils/apiConfig.ts` — `apiFetch()` wrapper.

## Conventions
- CSS design tokens from `globals.css` — never hardcode colors / rgba.
- Surface scale (solid): `--surface-base` < `--surface-subtle` < `--surface-elevated` < `--surface-muted` < `--surface-hover`. Cards: `--card-bg` + `--border-subtle` + `--card-shadow` (no backdrop-filter).
- Tints: `--primary-tint-{5,10,15,20}`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`. Single brand hue indigo `hsl(234,89%,64%)` via `--primary-hsl`. Secondary/accent reserved for charts.
- Theme: dark default + `@media (prefers-color-scheme: light)` + `[data-theme="light"]` override. Modal backdrop: `.modal-backdrop` (driven by `--modal-backdrop-bg`). **Backdrop scrolls when content > viewport**: `align-items: flex-start` + `overflow-y: auto` + `padding: clamp(1rem, 4vh, 4rem) 1rem`; inner panel must opt into internal scroll via `.card`/`.modal-content` descendant rule (`max-height: calc(100dvh - 2rem)` + `overflow-y: auto`). Do NOT inline `style={{ padding: ... }}` on `.modal-backdrop` — overrides the CSS scroll-pad. Fix `fix/responsive-qa-defects` (#566666f, RESP-006 root cause).
- Glass tokens (`--glass-*`) are legacy aliases mapped to solid surfaces — prefer solid names.
- 44px min touch target (`--touch-target-min`). Z-index: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500.
- Modals: `role="dialog"` + `aria-modal="true"` + `aria-labelledby` + ESC handler. Icon-only buttons need `aria-label`.
- Responsive breakpoints (canonical, post-`fix/responsive-qa-defects`): `width < 1024` = mobile/tablet drawer (Sidebar `isMobile=true`, `mobile-header` visible, hamburger trigger); `1024 ≤ width ≤ 1280` = icon-only desktop sidebar (80px wide); `width > 1280` = full sidebar (280px). `.header-actions` wraps at `≤ 1024` via `flex-wrap: wrap` + `row-gap` (never `overflow-x: auto` — parent `overflow: hidden` clips the scroll, see RESP-005/-011/-017 root cause).
- No `any` in TS. No gradient text / `linear-gradient` on UI chrome / `backdrop-filter` blur (mobile drawer overlay only). Mobile sidebar via `transform: translateX()`, never `left:`. `prefers-reduced-motion: reduce` honored globally.

## Next 16 prerender + `useSearchParams`
- `app/page.tsx` is `'use client'` + uses `useSearchParams()`. Next 16 requires `<Suspense>` wrap so `next build` can prerender without CSR bailout. Default export = `<Suspense fallback={null}><DashboardInner /></Suspense>`. Removing → `missing-suspense-with-csr-bailout` hard deploy blocker on Render `npm run build`.
- Local dev uvicorn ships `server: uvicorn`; Dockerfile CMD adds `--no-server-header`. Next.js proxy strips upstream `server` (belt-and-braces).

## Cross-page navigation contract
Dashboard owns modal + view-filter state; non-dashboard pages navigate to `/` with query params, dashboard consumes-then-strips: `/?openSettings=1`, `/?openDiscovery=1`, `/?view=audited|high-risk`, `/?search=<term>` (bridge translates to `?q=` on consume). Setters passed to Sidebar on non-dashboard pages MUST respect the `(open)` arg: `(open) => { if (open) router.push('/?openSettings=1') }` — else Sidebar's `setShowDiscoveryModal(false)` would navigate to `/?openDiscovery=1` and open wrong modal.

## Handler robustness pattern
Every state-changing handler hitting `/api/proxy/*` MUST: (1) check `res.ok` → surface `data.detail || data.error || \`<Action> failed (HTTP ${status})\`` via `showToast(..., 'error')`; (2) try/catch with network-failure toast; (3) `aria-busy` + `disabled` on trigger during inflight, reset in `finally` (rapid clicks otherwise fire duplicate Gemini calls — cost real money); (4) destructive ops (`processAll`, `startMassivePipeline`, `handleDeepHuntAll`, `handleClearLeads`) gate with `confirm()` naming count + one-line cost warning.

Pydantic 422 = `{detail: [{type, loc, msg, input, ctx}]}`. `AIChat.handleSubmit` joins `detail[].msg` so user sees "String should have at most 4000 characters" not generic placeholder.

## Frontend hardening
- Outreach modal `mailto:` href: `encodeURIComponent` lead email + subject + body.
- Dep pinning: `package.json` drops `^` on `next`, `@supabase/ssr`, `@supabase/supabase-js`. `postcss` override pinned `^8.5.10`.
- Login brute-force (`frontend/utils/loginThrottle.ts`): 5/60s per-IP. `MAX_BUCKETS=10_000` hard cap + oldest-eviction.
- Proxy `BACKEND_URL` scheme assertion: `_assertBackendSchemeAllowed` runs at request time (not module load — would crash `next build` against dev backend). Prod requires `https://` unless loopback (`127.0.0.1`, `localhost`, `*.localhost`).
