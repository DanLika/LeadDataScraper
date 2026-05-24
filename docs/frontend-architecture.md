# Frontend Architecture, Conventions, Cross-repo strategy

## Frontend Architecture
- `frontend/app/page.tsx` — Main dashboard. Cursor-pagination state (`leads`,
  `nextCursor`, `hasMore`) + `loadMoreLeads`. Heavy children lazy-loaded via
  `next/dynamic`: `HealthChart` (recharts), `AIChat`, `LeadTable`.
- `frontend/app/insights/page.tsx` — Analytics & AI strategic analysis. Recharts
  panels extracted to `InsightsCharts` and lazy-loaded; `AIChat` also dynamic.
  Hits `/leads?limit=200` for client-side aggregation snapshots.
- `frontend/app/campaigns/page.tsx` — Outreach campaign management. `AIChat`
  lazy.
- `frontend/app/components/LeadTable.tsx` — Virtualized lead inventory.
  `@tanstack/react-virtual`, CSS-grid rows (not `<table>` — virtualizer needs
  absolute positioning), sticky header, variable row heights via
  `measureElement`, 20-row overscan. Owns the "Load more" button + the
  auxiliary `last_error` / `key_offerings` / `pain_points` panel. Defines
  `cleanMarkdown` + `CollapsibleText` (moved here from page.tsx).
- `frontend/app/components/InsightsCharts.tsx` — PieChart + BarChart extracted
  from `/insights` so recharts (~80 KB gz) loads via the lazy chunk, not the
  initial bundle.
- `frontend/app/components/WebVitalsReporter.tsx` — `useEffect` registers
  CLS / INP / LCP / FCP / TTFB callbacks; `navigator.sendBeacon` to
  `/api/proxy/metrics`. Renders nothing. Mounted in `app/layout.tsx`.
- `frontend/app/components/AIChat.tsx` — Floating AI chat assistant
- `frontend/app/components/Sidebar.tsx` — Navigation sidebar with insights widget
- `frontend/app/components/HealthChart.tsx` — PieChart health breakdown + stats grid
- `frontend/app/components/StatsCards.tsx` — 4 summary stat cards (Total, Pending, Risk, Healthy)
- `frontend/app/components/FilterBar.tsx` — Search, segment, status, and score filters
- `frontend/app/types/lead.ts` — Shared `Lead` interface. Imported by both
  `page.tsx` and `LeadTable.tsx`; two identically-named interfaces in
  different files would be nominally distinct and break callback variance
  when passed across the file boundary.
- `frontend/app/globals.css` — Design tokens and global styles. NOTE:
  `--font-main: 'Inter'` is declared but Inter is NOT actually loaded
  (no `next/font/google` import, no `.woff*` files). App falls through to
  `system-ui`. Either drop `'Inter'` from the stack or wire
  `next/font/google` with `display: 'swap'`.
- `frontend/utils/apiConfig.ts` — API base URL, API key, and `apiFetch()` authenticated fetch wrapper

## Frontend Conventions
- Use CSS custom properties (design tokens) from `globals.css` — never hardcode colors
- Surface scale (solid, not glass): `--surface-base` < `--surface-subtle` < `--surface-elevated` < `--surface-muted` < `--surface-hover`
- Card surfaces use `--card-bg` + `--border-subtle` + `--card-shadow` (no backdrop-filter)
- Border scale: `--border-subtle`, `--border`, `--border-muted`
- Color tint tokens: `--primary-tint-5/10/15/20`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`
- Single brand hue: indigo `hsl(234, 89%, 64%)` via `--primary-hsl`. Secondary/accent reserved for charts only.
- Theming: dark default, light theme auto-applied via `@media (prefers-color-scheme: light)` and overridable with `[data-theme="light"]` on `:root`. Never hardcode rgba — all tokens flip between themes.
- Modal backdrop: use `.modal-backdrop` class (driven by `--modal-backdrop-bg`), never inline rgba
- Glass tokens (`--glass-bg`, `--glass-border`, `--glass-hover`) are legacy aliases mapped to solid surfaces — prefer the solid-surface names in new code
- All interactive elements must meet 44px minimum touch target (`--touch-target-min`)
- Z-index scale: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500
- Modals require: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, ESC key handler
- All buttons need `aria-label` when icon-only
- No `any` types in TypeScript — define proper interfaces
- Font: Inter (not Outfit or other AI-trendy fonts)
- No gradient text, no `linear-gradient` on UI chrome, no `backdrop-filter` blur (kept only on mobile drawer overlay)
- Mobile sidebar slides via `transform: translateX()`, never `left:` (avoid layout-triggering transitions)
- `prefers-reduced-motion: reduce` honored globally — disables all animations/transitions

## Available Design Skills (Impeccable)
Installed via `npx skills add pbakaus/impeccable`. Use as slash commands:
/polish, /audit, /animate, /bolder, /quieter, /distill, /critique, /colorize,
/harden, /delight, /clarify, /adapt, /onboard, /normalize, /extract,
/teach-impeccable, /optimize, /overdrive, /arrange, /typeset, /frontend-design

## Cross-repo strategy (BookBed.io)

LDS is internal tooling — `OPERATOR_EMAIL` single-tenancy is deliberate
(see [ADR-001](docs/adr/001-single-tenant-by-design.md)). The commercial
SaaS lives in two sibling repos under `~/git/`:
- `bookbed-website/` — Next.js 16 marketing site (Firebase App Hosting).
  Already heavily hardened (CSP, JsonLd `</script>`-escape, iCal-checker
  SSRF guard with double-resolve DNS-rebind protection). **Ahead of LDS**
  on `object-src 'none'` / `base-uri 'self'` / `form-action 'self'
  mailto:` / COOP / CORP / `X-Permitted-Cross-Domain-Policies`.
- `bookbed/` — Flutter SaaS app + Firebase Cloud Functions (TypeScript).
  Firestore + Stripe LIVE + Resend + `firebase_ai` Gemini chat (`gemini-
  2.5-flash-lite` in `ai_chat_provider.dart`). The real revenue surface.

[`docs/bookbed-crossover.md`](docs/bookbed-crossover.md) is the
**gap-analysis** that decides which LDS hardening patterns get ported to
which BookBed surface, which are already covered there, and which don't
apply. Three buckets: lead-gen specific (scrapers, agentic router,
outreach scoring — **never port**), cross-applicable security
(per-pattern table), CI workflow set (LDS has 19, bookbed-website has 1,
bookbed has 3 — biggest gap). Every ✅ row in the gap table is
file-verified (spot-checks listed in the appendix). Rows marked ⚠️/`?`
are hypothesis-only — re-verify before porting.

Phased action plan in that doc: **A** bookbed-website CI hardening
(~1 day — port LDS's `ci.yml` + `security.yml` + `workflow-drift.yml` +
dependabot, all action SHAs pinned with `# vX.Y.Z`) → **B** bookbed CF
email CRLF guards on Resend (~4h — recipient regex with explicit
CRLF reject, subject/from_name CRLF assert before MIME write) → **C**
bookbed Flutter Gemini `<UNTRUSTED_DATA>` fence around user chat input
(~1 day — currently flows raw to `_chatSession.sendMessageStream`,
only static KB system instruction) → **D** backport newer headers from
bookbed-website back to LDS (~30min) → **E** long tail (cost report,
cold-start monitor, synthetic monitor, Firestore orphan sweep).

**Phase 13 of the LDS roadmap was scoped to a dogfood-only cut on
2026-05-22**: ship 13.14 (this crossover doc, **DONE**), then 13.1
hr-HR i18n via `next-intl`, 13.3 demo seed + `is_demo` column, 13.5
DKIM/SPF/DMARC for the sending domain, 13.4 email dispatch wiring
`email_sender.py`, 13.15 two-week dogfood with real Croatian leads.
The commercial items (Stripe billing, usage metering, multi-tenancy
migration, public landing, signup, feedback widget, Plausible
analytics) belong in the BookBed repos — see "Later (3–6 months) >
[BookBed.io] Commercialization track" in
[`docs/roadmap.md`](docs/roadmap.md) and the Phase A→E actions in the
crossover doc above.

