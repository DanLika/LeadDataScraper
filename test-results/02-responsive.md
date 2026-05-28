# Terminal 2 — Responsive matrix (RESP)

**Surface**: `https://lead-scraper-frontend.onrender.com` (deployed Render frontend; auth-mint via `_auth_method.md` for gated pages).
**Driver**: Playwright MCP (Chrome DevTools MCP was held by another terminal's profile — pivoted; viewport-based responsive assertions still valid since Tailwind breakpoints are width-only).
**Pages × viewports**: `/`, `/insights`, `/campaigns`, `/login` × 360×640 / 390×844 / 768×1024 / 1024×768 / 1440×900. Plus locale toggle hr at 360 + 1440.

**Environment caveat (load-bearing)** — FE proxy returns `HTTP 502` for every `/api/proxy/{leads,stats,insights,campaigns,orchestrator,metrics}` call. Documented in `test-results/_auth_method.md` L196-198: FE `BACKEND_URL` env points at the pre-migration Render host (the live backend has the `-x51l` suffix after the new-account migration). Operator action required. Impact on this terminal: skeleton states render but data-driven panels (LeadTable rows, recharts pie/bar, campaign list, AI Insights) do not populate — chart **resize** assertions degrade to SKIP. Layout / overflow / sidebar / touch-target / clip assertions remain valid against the shell.

**Repeat findings** (suppressed in per-row Detail to keep table readable; named once here):
- **F-CHAT-TOUCH**: floating AI-chat panel's "Clear Chat" (78×32 px) + "Minimize" (68×32 px) buttons under 44×44 px on every authed viewport. Same 11.2 px font.
- **F-CHAT-FONT**: same two labels render at 11.2 px (< 12 px floor).
- **F-DASH-TOOLBAR-CLIP**: top toolbar `Audit All / AI Orchestrate / Hunt All / Export Full / CRM Export / Import CSV` uses non-wrapping flex; at ≤1024 px the last 1–4 buttons extend past the viewport's right edge despite no document overflow (parent has `overflow:hidden`, so it doesn't trigger horizontal scroll but visually clips — confirmed in screenshots).
- **F-SB-DESKTOP**: sidebar drawer threshold ≥ ~1280 px. At 1024×768 (small desktop) the sidebar is still hidden behind a hamburger which is unusual for a desktop dashboard. Pass at 1440.
- **F-MODAL-MOBILE-CLIP**: Settings modal (`role="dialog"` "System Settings") at 360 wide renders inner panel 288×841 px with `top=-101 px` and backdrop `overflow-y: visible`. Header + Close button hidden above viewport, no scrollbar generated. Critical mobile bug — user cannot dismiss without keyboard ESC.
- **F-ZINDEX-CHAT-EMPTY**: AI chat overlay (z-index 400) covers empty-state body text on `/campaigns` mobile. Backdrop should drop opacity or relayout copy above the chat dock.

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| RESP-001 | Overflow | / @ 360×640 | scrollWidth (352) ≤ clientWidth (352) | PASS | `docW==clientW`; no horizontal scroll |
| RESP-002 | Sidebar | / @ 360×640 | drawer mode, hamburger present | PASS | `aside.left=-280`, "Open menu" button visible |
| RESP-003 | TouchTgt | / @ 360×640 | interactive ≥ 44×44 px | FAIL | F-CHAT-TOUCH. Also "Skip to main content" 1×1 — standard a11y skip link, excluded from counts |
| RESP-004 | Font | / @ 360×640 | text ≥ 12 px | FAIL | F-CHAT-FONT (Clear Chat / Minimize 11.2 px) |
| RESP-005 | Clip | / @ 360×640 | no main-region child right > innerW | FAIL | F-DASH-TOOLBAR-CLIP; `btn-primary` (AI Orchestrate) right=365 px, `btn-secondary` right=514 px past 360 — visible cut in `02-responsive-screens/dashboard-360x640.png` |
| RESP-006 | Modal | / @ 360×640 | Settings dialog fits viewport, has scroll on overflow | FAIL | F-MODAL-MOBILE-CLIP. Opened "Postavke" → dialog backdrop 352×640 fits but inner panel 288×**841 px**; panel positioned `top=-101` so header "System Settings" + Close button **above viewport** + invisible. Backdrop `overflow-y: visible` (no scrollbar), so user cannot scroll up to close. `aria-modal=true` correct; ESC works. See `02-responsive-screens/settings-modal-360x640.png` — visible content begins mid-"API Configuration" with header cut off |
| RESP-007 | Overflow | / @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=382, clientW=382 |
| RESP-008 | Sidebar | / @ 390×844 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-009 | TouchTgt | / @ 390×844 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-010 | Font | / @ 390×844 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-011 | Clip | / @ 390×844 | no main child right > innerW | FAIL | F-DASH-TOOLBAR-CLIP; Hunt All right=514 px past 390 — see `02-responsive-screens/dashboard-390x844.png` |
| RESP-012 | Modal | / @ 390×844 | dialogs fit | SKIP | no modal opened |
| RESP-013 | Overflow | / @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=760 |
| RESP-014 | Sidebar | / @ 768×1024 | tablet: drawer or expanded | PASS | drawer; acceptable at `md:` breakpoint |
| RESP-015 | TouchTgt | / @ 768×1024 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-016 | Font | / @ 768×1024 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-017 | Clip | / @ 768×1024 | no main child right > innerW | FAIL | F-DASH-TOOLBAR-CLIP; CRM Export right=865 px, Import CSV right=1038 px past 768 — see `02-responsive-screens/dashboard-768x1024.png` |
| RESP-018 | Modal | / @ 768×1024 | dialogs fit | SKIP | no modal opened |
| RESP-019 | Overflow | / @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1016 |
| RESP-020 | Sidebar | / @ 1024×768 | desktop: expanded | FAIL | F-SB-DESKTOP; aside.left=-280, hamburger still visible at small-desktop 1024 px width |
| RESP-021 | TouchTgt | / @ 1024×768 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-022 | Font | / @ 1024×768 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-023 | Clip | / @ 1024×768 | no main child right > innerW | PASS | toolbar wraps; clipCount=0 |
| RESP-024 | Modal | / @ 1024×768 | dialogs fit | SKIP | no modal opened |
| RESP-025 | Overflow | / @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1432 |
| RESP-026 | Sidebar | / @ 1440×900 | expanded | PASS | aside.left=0, width=280, hamburger hidden |
| RESP-027 | TouchTgt | / @ 1440×900 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-028 | Font | / @ 1440×900 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-029 | Clip | / @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-030 | Modal | / @ 1440×900 | Settings dialog fits viewport | PASS | Opened "Postavke" → dialog 1432×900, `aria-modal=true`, fits height, ESC closes; visible inner card centred at ~440×750 — see `02-responsive-screens/settings-modal-1440x900.png` |
| RESP-030a | Chart | / @ 360×640 | HealthChart (recharts) resizes | PASS | aria-label="Lead health breakdown chart" measures 222×240 px inside 288 px main column; no horizontal overflow. Recharts `ResponsiveContainer` working at mobile width even with empty data (chart renders zero-state arcs) |
| RESP-030b | Chart | / @ 1440×900 | HealthChart resizes | PASS | chart 350×240 px inside 1088 px container; no clip. Resize sweep 360→1440 shows fluid width |
| RESP-031 | Overflow | /insights @ 360×640 | scrollWidth ≤ clientWidth | PASS | docW=352 |
| RESP-032 | Sidebar | /insights @ 360×640 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-033 | TouchTgt | /insights @ 360×640 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH (global chat overlay) |
| RESP-034 | Font | /insights @ 360×640 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-035 | Clip | /insights @ 360×640 | no main child right > innerW | PASS | clipCount=0 — see `02-responsive-screens/insights-360x640.png` |
| RESP-036 | Charts | /insights @ 360×640 | recharts resize without break | SKIP | `/api/proxy/insights` HTTP 502; recharts panels render no SVG; resize sweep meaningless without data |
| RESP-037 | Overflow | /insights @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=382 |
| RESP-038 | Sidebar | /insights @ 390×844 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-039 | TouchTgt | /insights @ 390×844 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-040 | Font | /insights @ 390×844 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-041 | Clip | /insights @ 390×844 | no main child right > innerW | PASS | clipCount=0 |
| RESP-042 | Charts | /insights @ 390×844 | recharts resize | SKIP | 502 |
| RESP-043 | Overflow | /insights @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=760 |
| RESP-044 | Sidebar | /insights @ 768×1024 | drawer | PASS | aside.left=-280 |
| RESP-045 | TouchTgt | /insights @ 768×1024 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-046 | Font | /insights @ 768×1024 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-047 | Clip | /insights @ 768×1024 | no main child right > innerW | PASS | clipCount=0 |
| RESP-048 | Charts | /insights @ 768×1024 | recharts resize | SKIP | 502 |
| RESP-049 | Overflow | /insights @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1016 |
| RESP-050 | Sidebar | /insights @ 1024×768 | expanded | FAIL | F-SB-DESKTOP |
| RESP-051 | TouchTgt | /insights @ 1024×768 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-052 | Font | /insights @ 1024×768 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-053 | Clip | /insights @ 1024×768 | no main child right > innerW | PASS | clipCount=0 |
| RESP-054 | Charts | /insights @ 1024×768 | recharts resize | SKIP | 502 |
| RESP-055 | Overflow | /insights @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1432 |
| RESP-056 | Sidebar | /insights @ 1440×900 | expanded | PASS | aside.left=0 |
| RESP-057 | TouchTgt | /insights @ 1440×900 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH |
| RESP-058 | Font | /insights @ 1440×900 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-059 | Clip | /insights @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-060 | Charts | /insights @ 1440×900 | recharts resize | SKIP | 502 |
| RESP-061 | Overflow | /campaigns @ 360×640 | scrollWidth ≤ clientWidth | PASS | docW=352 |
| RESP-062 | Sidebar | /campaigns @ 360×640 | drawer + mobile-top-bar | PASS | aside.left=-280; campaigns page renders own mobile header with "Open menu" |
| RESP-063 | TouchTgt | /campaigns @ 360×640 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH PLUS "Back to dashboard" icon link 48×20 px (h=20 under 44 floor) — see `02-responsive-screens/campaigns-360x640.png` |
| RESP-064 | Font | /campaigns @ 360×640 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-065 | Clip | /campaigns @ 360×640 | no main child right > innerW | PASS | clipCount=0; "New Campaign" CTA fits |
| RESP-066 | Tables | /campaigns @ 360×640 | mobile table stack/scroll | SKIP | empty list (502); empty-state card renders centred |
| RESP-066a | Z-index | /campaigns @ 360×640 | AI chat overlay does not cover empty-state copy | FAIL | F-ZINDEX-CHAT-EMPTY. AI chat panel (`region "AI assistant"`, z-index 400 per CLAUDE.md) overlaps "No Campaigns — Create a campaign to start reaching d…" copy. Overlay opaque pill-bar at bottom cuts the message mid-character. See `02-responsive-screens/campaigns-360x640.png`. Layout bug independent of 502 |
| RESP-067 | Overflow | /campaigns @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=390 |
| RESP-068 | Sidebar | /campaigns @ 390×844 | drawer | PASS | aside.left=-280 |
| RESP-069 | TouchTgt | /campaigns @ 390×844 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH + Back-to-dashboard 48×20 |
| RESP-070 | Font | /campaigns @ 390×844 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-071 | Clip | /campaigns @ 390×844 | no main child right > innerW | PASS | clipCount=0 |
| RESP-072 | Tables | /campaigns @ 390×844 | empty state fits | SKIP | 502 |
| RESP-073 | Overflow | /campaigns @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=768 |
| RESP-074 | Sidebar | /campaigns @ 768×1024 | drawer | PASS | aside.left=-280 |
| RESP-075 | TouchTgt | /campaigns @ 768×1024 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH + Back-to-dashboard |
| RESP-076 | Font | /campaigns @ 768×1024 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-077 | Clip | /campaigns @ 768×1024 | no main child right > innerW | PASS | clipCount=0 |
| RESP-078 | Tables | /campaigns @ 768×1024 | tablet table mode | SKIP | 502 |
| RESP-079 | Overflow | /campaigns @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1024 |
| RESP-080 | Sidebar | /campaigns @ 1024×768 | expanded | FAIL | F-SB-DESKTOP |
| RESP-081 | TouchTgt | /campaigns @ 1024×768 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH + Back-to-dashboard |
| RESP-082 | Font | /campaigns @ 1024×768 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-083 | Clip | /campaigns @ 1024×768 | no main child right > innerW | PASS | clipCount=0 |
| RESP-084 | Tables | /campaigns @ 1024×768 | desktop table mode | SKIP | 502 |
| RESP-085 | Overflow | /campaigns @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1440 |
| RESP-086 | Sidebar | /campaigns @ 1440×900 | expanded | PASS | aside.left=0 |
| RESP-087 | TouchTgt | /campaigns @ 1440×900 | ≥ 44×44 px | FAIL | F-CHAT-TOUCH + Back-to-dashboard |
| RESP-088 | Font | /campaigns @ 1440×900 | ≥ 12 px | FAIL | F-CHAT-FONT |
| RESP-089 | Clip | /campaigns @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-090 | Tables | /campaigns @ 1440×900 | desktop table mode | SKIP | 502 |
| RESP-091 | Overflow | /login @ 360×640 | scrollWidth ≤ clientWidth | PASS | docW=360; anon page, no sidebar |
| RESP-092 | Sidebar | /login @ 360×640 | none expected | PASS | login layout has no sidebar |
| RESP-093 | TouchTgt | /login @ 360×640 | ≥ 44×44 px | PASS | 3 interactives (email/pwd input + submit) all ≥44 px |
| RESP-094 | Font | /login @ 360×640 | ≥ 12 px | PASS | tinyFontCount=0 |
| RESP-095 | Form | /login @ 360×640 | fits viewport | PASS | form 312×353 px inside 360×640 viewport |
| RESP-096 | Form | /login @ 390×844 | fits | PASS | form 342×353 px |
| RESP-097 | Overflow | /login @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=390; touch+font PASS |
| RESP-098 | Form | /login @ 768×1024 | caps at 380 px wide, centred | PASS | form 380×353 px |
| RESP-099 | Overflow | /login @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=768; touch+font PASS |
| RESP-100 | Form | /login @ 1024×768 | caps at 380 px wide, centred | PASS | form 380×353 px; no overflow |
| RESP-101 | Form | /login @ 1440×900 | caps at 380 px wide, centred | PASS | form 380px wide; no overflow |
| RESP-102 | Locale | /  @ 360×640 (hr) | mobile drawer accommodates "Nadzorna ploča" | PASS | drawer opened via hamburger; 7 nav items 223×45 px each, longest label "Nadzorna ploča" 113 px fits within 223 row; `02-responsive-screens/locale-hr-360x640-drawer.png` shows Croatian labels stacked cleanly |
| RESP-103 | Locale | / @ 1440×900 (hr) | sidebar 280 px accommodates hr labels | PASS | aside.width=280, labelRows all 223×44, sbClip=0, overflow=false |
| RESP-104 | Locale | hr translation coverage | nav labels translate | PASS-PARTIAL | "Dashboard→Nadzorna ploča" ✓, "Insights→Uvidi" ✓, "Settings→Postavke" ✓, "Sign Out→Odjava" ✓, "Language→Jezik" ✓. **NOT translated**: "Deep Discovery" / "Audited" / "High Risk" / h1 "Pipeline Intelligence" — translation file gap, layout-safe (no breakage). i18n hr.json incomplete (consistent with [[lds_i18n_cookie_decision]] note that hr.json is machine-quality + needs native review) |

## Summary

5 viewports × 4 pages + chart probes + modal probes + locale rows = **108 rows** recorded. Concrete defects (excluding F-CHAT repeats reported across rows):

1. **F-MODAL-MOBILE-CLIP** *(critical)* — Settings modal at 360 wide places its inner panel at `top=-101 px` with `overflow-y: visible` backdrop. Header + Close button + Save button slip above viewport, no scrollbar appears, user can't reach them. **Fix**: `overflow-y: auto` on the backdrop + `align-items: flex-start` + `pt-4` on the dialog wrapper so tall panels start at the top, or constrain panel to `max-h: 100dvh` with `overflow-y: auto` on the panel itself.
2. **F-DASH-TOOLBAR-CLIP** — top toolbar non-wrapping flex at / ≤ 1024 px. Viewport-visible cut at 360/390/768 confirmed by screenshot. **Fix**: `flex-wrap` + `gap-y` on toolbar container.
3. **F-SB-DESKTOP** — sidebar drawer mode persists at 1024 (small desktop). Hamburger visible where users expect a sidebar. **Fix**: lower drawer/expanded breakpoint from current ~1280 to 1024, or add intermediate icons-only mode.
4. **F-CHAT-TOUCH / F-CHAT-FONT** — AI assistant panel's "Clear Chat" + "Minimize" controls under 44×44 px and under 12 px font, on every authed viewport. **Fix**: bump label font to 12 px and increase button height to 44 px (or convert to icon buttons with 44×44 hit area + visible-on-focus label).
5. **F-CAMPAIGNS-BACK-LINK** — "Back to dashboard" arrow icon link is 48×20 px (h=20). Visible at all viewports on /campaigns. **Fix**: enlarge to ≥44 px hit area or wrap in a `Link` with padding.
6. **F-ZINDEX-CHAT-EMPTY** — AI chat overlay z-index 400 covers /campaigns empty-state text on mobile (visible in 360 screenshot). **Fix**: empty-state container `mb-24` (above chat dock height) or shrink chat dock to icon-only when empty state present.
7. **F-LOCALE-PARTIAL** — hr.json missing keys for `Deep Discovery / Audited / High Risk / Pipeline Intelligence` (sample showed h1 still EN). Not a layout fail; record for translator pass. Cross-ref [[lds_i18n_cookie_decision]].

**Positive findings**:
- HealthChart (recharts) IS responsive: 222 px @ 360 → 350 px @ 1440. Chart container resize works even with empty data state (zero-arc render). RESP-030a / RESP-030b.
- Login form caps at 380 px and centres at every viewport; touch targets + font compliant at 360. RESP-091..101.
- Croatian locale (hr) drawer at 360 cleanly fits all 7 nav items at 223×45 px; longest label "Nadzorna ploča" 113 px within 223 row. RESP-102.

**Skipped (env-blocked)**: charts on /insights, table responsive matrix on /campaigns + dashboard inventory, AI Insights cards — all due to FE→BE proxy HTTP 502 (operator action item, not a responsive defect; see `_auth_method.md` L196-198 for fix scope).

Screenshots (FAIL evidence): `test-results/02-responsive-screens/{dashboard-360x640,dashboard-390x844,dashboard-768x1024,insights-360x640,campaigns-360x640,locale-hr-360x640-drawer,settings-modal-1440x900,settings-modal-360x640}.png`.
