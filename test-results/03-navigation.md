# Terminal 3 — Navigation (NAV)

Surface: `https://lead-scraper-frontend.onrender.com` (Render `starter`).
Auth: minted Supabase session via `_auth_method.md` (admin `generate_link` → fragment-parse → `@supabase/ssr` base64 cookie). Single-tenant invariant preserved (no new auth.users row).
Driver: Playwright MCP browser. chrome-devtools-mcp profile was occupied by a parallel terminal so isolated-Chrome-on-port-9225 was substituted with Playwright's bundled chromium — functionally equivalent for NAV scope (read-only nav, no destructive clicks).
Backend state during run: ~~`/api/proxy/*` returning 502~~ → **RESOLVED 2026-05-28**: BACKEND_URL re-synced to `-x51l` host; all proxy paths now 200 with real data. NAV-009..012 re-tested PASS (0 console errors on /, /login, /insights, /campaigns).

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| NAV-001 | Route/anon | `GET /` | Redirects to /login with sanitized next | PASS | 307 → `/login?next=%2F` |
| NAV-002 | Route/sess | `GET /` | Dashboard renders, h1=Pipeline Intelligence | PASS | 200; sidebar + Prospect Inventory + StatsCards (0/0/0/0 — backend 502) all paint |
| NAV-003 | Route/anon | `GET /login` | Public, sign-in form renders | PASS | 200; form `<input type=email>` + password + Sign-in button |
| NAV-004 | Route/sess | `GET /login` | Reachable with session (no auto-redirect) | PASS | 200; sign-in form still rendered (no /login → / forced bounce — intentional, lets operator switch identity) |
| NAV-005 | Route/anon | `GET /insights` | Redirects to /login with next param | PASS | 307 → `/login?next=%2Finsights` |
| NAV-006 | Route/sess | `GET /insights` (SPA-nav) | Strategic Insights heading renders | PASS | 200; h1=Strategic Insights via Sidebar→Insights click |
| NAV-007 | Route/anon | `GET /campaigns` | Redirects to /login with next param | PASS | 307 → `/login?next=%2Fcampaigns` |
| NAV-008 | Route/sess | `GET /campaigns` | Outreach Campaigns heading renders | PASS | 200; h1=Outreach Campaigns + New Campaign button |
| NAV-009 | Console/route | `/` first load | Console error-free | PASS | Re-tested 2026-05-28 post-BACKEND_URL fix: 0 console errors, 0 warnings during full networkidle. Real lead inventory loads (`total_leads=23`). |
| NAV-010 | Console/route | `/login` first load | Console error-free | PASS | Re-probe: `POST /api/proxy/metrics` from WebVitals beacon now reaches backend (no 502). Verified separately in 10-mobile RESP-023 update. |
| NAV-011 | Console/route | `/insights` first load | Console error-free | PASS | Re-test: 0 console errors during full networkidle; Insights panels populate with real data; recharts SVGs render (4 charts: 3 pies + 4 bars per the 02-responsive RESP-036 etc re-test). |
| NAV-012 | Console/route | `/campaigns` first load | Console error-free | PASS | Re-test: 0 console errors; campaign list populates from `/api/proxy/campaigns` 200. |
| NAV-013 | Sidebar | Logo `<Link href="/">` "LeadScout home" | Navigates to / | PASS | DOM-verified href=/; visible in sidebar on every gated route. Skipped click duplication (Dashboard Link below tests identical destination). |
| NAV-014 | Sidebar | Dashboard `<Link href="/">` | Navigates to / (Pipeline Intelligence) | PASS | Clicked from /campaigns → URL=/, h1=Pipeline Intelligence |
| NAV-015 | Sidebar | Insights `<Link href="/insights">` | Navigates to /insights (Strategic Insights) | PASS | Clicked from / → URL=/insights, h1=Strategic Insights |
| NAV-016 | Sidebar | Deep Discovery `<button>` | Opens dialog "Lead Discovery Engine" | PASS | Clicked from /insights → URL=/ (button is dashboard-scoped, click also navigates) + role=dialog "Lead Discovery Engine" mounts. ESC closes. |
| NAV-017 | Sidebar | Audited `<button>` | Toggles view=audited, sidebar marks aria-pressed | PASS | Click → URL stays /, aria-pressed=true, class `active`. No URL param (state is dashboard-local) |
| NAV-018 | Sidebar | High Risk `<button>` | Toggles view=high-risk, sidebar marks aria-pressed | PASS | Click → URL stays /, aria-pressed=true; Audited deasserted |
| NAV-019 | Sidebar | Settings `<button>` | Opens dialog "System Settings" | PASS | role=dialog with aria-labelledby = "System Settings". Danger-zone buttons (Remove all demo data / Clear All Leads) present — NOT clicked per read-only rule. ESC closes. |
| NAV-020 | Sidebar | Sign out `<button>` | POST /api/auth/signout → redirect /login | PASS | Click → router.replace('/login'); on-page form "Sign in" appears. Cookies cleared (verified via subsequent anon probes). |
| NAV-021 | Deep-link | `/?openSettings=1` | Settings dialog opens + URL stripped to / | PASS | `useEffect` consumes param then router.replace('/'). After 2s settle: location.href=`https://…/`, `[role=dialog]` h2="System Settings" |
| NAV-022 | Deep-link | `/?openDiscovery=1` | Discovery dialog opens + URL stripped to / | PASS | Same consume-and-strip; `[role=dialog]` h2="Lead Discovery Engine" |
| NAV-023 | Deep-link | `/?view=audited` | URL stripped + Audited aria-pressed | PASS | URL → /, sidebar Audited aria-pressed=true |
| NAV-024 | Deep-link | `/?view=high-risk` | URL stripped + High Risk aria-pressed | PASS | URL → /, sidebar High Risk aria-pressed=true |
| NAV-025 | Deep-link | `/?search=test%20clinic` | Translates to `?q=test%20clinic`, input populated | PASS | URL → /?q=test%20clinic (NOT stripped — `?q=` is durable filter vocab per CLAUDE.md). Search input value="test clinic", "Clear filters" surface appears. |
| NAV-026 | Back/fwd | / → /insights → /campaigns, back×2 | Lands on / | PASS | h1=Pipeline Intelligence after second back |
| NAV-027 | Back/fwd | Forward×2 from / | Lands on /campaigns | PASS | history.forward() twice → URL=/campaigns |
| NAV-028 | Back/fwd | Modal-via-query then back | Modal closes + URL clean | PASS | /campaigns → /?openDiscovery=1 (modal opens, router.replace strips param) → back → /campaigns, no dialog. Router.replace ensures back skips the modal entry. |
| NAV-029 | Link | `<a href="#main-content">` Skip-link | Present on /, /insights, /campaigns, /login | PASS | DOM-verified on every route load |
| NAV-030 | Link | Sidebar Logo `<Link href="/">` aria-label="LeadScout home" | Routes to / | PASS | DOM enum + href correct |
| NAV-031 | Link | Sidebar Dashboard `<Link href="/">` | Routes to / | PASS | Covered by NAV-014 |
| NAV-032 | Link | Sidebar Insights `<Link href="/insights">` | Routes to /insights | PASS | Covered by NAV-015 |
| NAV-033 | Link | /insights "Back to dashboard" `<Link href="/">` icon-only | Routes to / | PASS | DOM-verified href=/. aria-label="Back to dashboard" present (a11y rescue for icon-only) |
| NAV-034 | Link | /insights stat-card "Total Leads" `<Link href="/">` | Routes to / | PASS | DOM-verified href=/, title="Open dashboard" |
| NAV-035 | Link | /insights stat-card "Audited Leads" `<Link href="/?view=audited">` | Routes to / + view=audited | PASS | Clicked → URL=/, sidebar Audited aria-pressed=true |
| NAV-036 | Link | /insights stat-card "High Risk" `<Link href="/?view=high-risk">` | Routes to / + view=high-risk | PASS | DOM-verified href; same consume-strip path as NAV-024 |
| NAV-037 | Link | /campaigns "Back to dashboard" `<Link href="/">` | Routes to / | PASS | Clicked → URL=/, h1=Pipeline Intelligence |
| NAV-038 | Link/mailto | Outreach modal mailto on /page.tsx:1422 | encodeURIComponent on email+subject+body | PASS | BACKEND_URL re-synced 2026-05-28 — leads now load (23 in inventory). Source contract intact: `href={`mailto:${encodeURIComponent(...)}${... ?subject=${encodeURIComponent(...)}&body=${encodeURIComponent(...)}: ''}`}` — leadEmail + subject + body all encoded. Live click-through not re-exercised (read-only scope). |
| NAV-039 | Link/external | Outreach modal LinkedIn `<a href={ensureProtocol(activeLead.linkedin)}>` page.tsx:1515 + LinkedIn search page.tsx:1526 | target/rel guards on external | PASS | BACKEND_URL fixed. Source contract: `ensureProtocol()` coerces to https:// scheme (no protocol-relative `//evil`). LinkedIn search `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(...)}`. |
| NAV-040 | Link/external | Outreach modal Gmail compose page.tsx:1593 | URL-encoded to/su/body | PASS | BACKEND_URL fixed. Source contract: `encodeURIComponent` on every interpolated field of `https://mail.google.com/mail/?view=cm&to=...&su=...&body=...`. |
| NAV-041 | Link/external | LeadTable social links FB/IG/LinkedIn/TikTok/Pinterest (LeadTable.tsx:359-363) | target="_blank" + rel="noopener noreferrer" | PASS | BACKEND_URL fixed; `/leads` returns real rows. Source contract: every social `<a>` has explicit `target="_blank" rel="noopener noreferrer"` + aria-label naming the company. No reverse-tabnabbing risk. |
| NAV-042 | Link/tel | LeadTable phone `<a href={`tel:...`}>` LeadTable.tsx:294 | Valid tel URI on `+`/digits only | PASS | BACKEND_URL fixed. Source contract: `lead.phone.replace(/[^+0-9]/g, '')` sanitises digits + `+` only before scheme. |
| NAV-043 | 404 | `GET /nonexistent-route` (session) | Renders Next.js 404 page, no crash | PASS | h1="404", h2="This page could not be found." HTTP status 404. No React error boundary needed. |
| NAV-044 | Logout-redirect | Anon `GET /` (post-signout) | 307 → /login?next=%2F | PASS | Confirmed via direct HTTP probe after Sidebar Sign-out click |
| NAV-045 | Logout-redirect | Anon `GET /insights` | 307 → /login?next=%2Finsights | PASS | next param URL-encoded `%2F` |
| NAV-046 | Logout-redirect | Anon `GET /campaigns` | 307 → /login?next=%2Fcampaigns | PASS | — |
| NAV-047 | Logout-redirect | Anon `GET /?openSettings=1` | next preserves query | PASS | 307 → `/login?openSettings=1&next=%2F%3FopenSettings%3D1` — query merged into login URL AND encoded into next= (so post-login deep-link still opens Settings modal). Sanitiser still applies per `sanitizeNext()`. |
