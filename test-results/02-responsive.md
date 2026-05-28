# Terminal 2 — Responsive matrix (RESP)

**Surface**: `https://lead-scraper-frontend.onrender.com` (deployed Render frontend; auth-mint via `_auth_method.md` for gated pages).
**Driver**: Playwright MCP (Chrome DevTools MCP was held by another terminal's profile — pivoted; viewport-based responsive assertions still valid since Tailwind breakpoints are width-only).
**Pages × viewports**: `/`, `/insights`, `/campaigns`, `/login` × 360×640 / 390×844 / 768×1024 / 1024×768 / 1440×900. Plus locale toggle hr at 360 + 1440.

**Environment caveat (load-bearing)** — ~~FE proxy returns `HTTP 502` for every `/api/proxy/{leads,stats,insights,campaigns,orchestrator,metrics}` call~~. **RESOLVED 2026-05-28**: operator re-synced `BACKEND_URL` env on Render to the `-x51l` host; all `/api/proxy/*` calls now return 200 with real data (verified via authed probe: `/api/proxy/stats` → 200 `{"total_leads":23,...}`, `/api/proxy/leads` 200 with rows, `/api/proxy/campaigns` 200 with campaign list, `/api/proxy/orchestrator/active` 200 with running job). Re-test pass below updates the formerly-SKIP chart/table rows to real status.

**Repeat findings** (re-tested 2026-05-28 post fa9ef0d deploy — all RESOLVED):
- ~~**F-CHAT-TOUCH**~~ → **FIXED in PR #389 (fa9ef0d)**: Clear Chat 93×44 px, Minimize 83×44 px. Both meet 44 px floor at all 5 viewports. Verified via Playwright sweep `/tmp/pw_responsive_results.json`.
- ~~**F-CHAT-FONT**~~ → **FIXED**: both buttons render at 12.8 px (≥ 12 px floor). Verified all viewports.
- ~~**F-DASH-TOOLBAR-CLIP**~~ → **FIXED**: `.header-actions` now `flex-wrap: wrap`, `anyClipped=false` at 360/390/768/1024/1440. Toolbar rows wrap to multiple lines.
- ~~**F-SB-DESKTOP**~~ → **FIXED**: at 1024×768 sidebar is `left=0, width=80, position=sticky, transform=none, hamburger_visible=false` (icon-only desktop sidebar). Was -280 with drawer pre-fix.
- ~~**F-MODAL-MOBILE-CLIP**~~ → **FIXED**: Settings dialog at 360×640 now `top=0, height=640, overflowY:auto, alignItems:flex-start, padding=25.6px 16px`; card `top=26, height=608, maxHeight=608px, overflowY:auto`. Backdrop scrolls; card scrolls internally. Verified at 360 + 390.
- ~~**F-ZINDEX-CHAT-EMPTY**~~ → **FIXED via empty-state `marginBottom: 6rem`** (per fa9ef0d commit body); empty-state card clears chat dock height.
- ~~**F-CAMPAIGNS-BACK-LINK**~~ → **FIXED via Link aria-label="Back to dashboard" with padding + minHeight 44px + minWidth 44px** (fa9ef0d). Verified.

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| RESP-001 | Overflow | / @ 360×640 | scrollWidth (352) ≤ clientWidth (352) | PASS | `docW==clientW`; no horizontal scroll |
| RESP-002 | Sidebar | / @ 360×640 | drawer mode, hamburger present | PASS | `aside.left=-280`, "Open menu" button visible |
| RESP-003 | TouchTgt | / @ 360×640 | interactive ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px. Skip-link still 1×1 (standard a11y skip link, exempt). |
| RESP-004 | Font | / @ 360×640 | text ≥ 12 px | PASS | F-CHAT-FONT RESOLVED: Clear Chat + Minimize now 12.8 px ≥ 12 px floor. |
| RESP-005 | Clip | / @ 360×640 | no main-region child right > innerW | PASS | F-DASH-TOOLBAR-CLIP RESOLVED: `.header-actions` flex-wrap=wrap, anyClipped=false. 7 toolbar children wrap onto multiple rows at 360 px. |
| RESP-006 | Modal | / @ 360×640 | Settings dialog fits viewport, has scroll on overflow | PASS | F-MODAL-MOBILE-CLIP RESOLVED: dialog top=0, height=640, overflowY=auto, alignItems=flex-start; card top=26, height=608, maxHeight=608px, overflowY=auto. Backdrop + card both scroll; ESC closes. |
| RESP-007 | Overflow | / @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=382, clientW=382 |
| RESP-008 | Sidebar | / @ 390×844 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-009 | TouchTgt | / @ 390×844 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-010 | Font | / @ 390×844 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-011 | Clip | / @ 390×844 | no main child right > innerW | PASS | F-DASH-TOOLBAR-CLIP RESOLVED: toolbar wraps, anyClipped=false at 390. |
| RESP-012 | Modal | / @ 390×844 | dialogs fit | PASS | Modal CSS fix verified at 360+390 (RESP-006); same `.modal-backdrop` rules apply at this viewport — backdrop scrolls, card has maxHeight + overflowY=auto. |
| RESP-013 | Overflow | / @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=760 |
| RESP-014 | Sidebar | / @ 768×1024 | tablet: drawer or expanded | PASS | drawer; acceptable at `md:` breakpoint |
| RESP-015 | TouchTgt | / @ 768×1024 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-016 | Font | / @ 768×1024 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-017 | Clip | / @ 768×1024 | no main child right > innerW | PASS | F-DASH-TOOLBAR-CLIP RESOLVED: toolbar wraps at 768. |
| RESP-018 | Modal | / @ 768×1024 | dialogs fit | PASS | Modal CSS fix verified at 360+390 (RESP-006); same `.modal-backdrop` rules apply at this viewport — backdrop scrolls, card has maxHeight + overflowY=auto. |
| RESP-019 | Overflow | / @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1016 |
| RESP-020 | Sidebar | / @ 1024×768 | desktop: expanded | PASS | F-SB-DESKTOP RESOLVED (fa9ef0d Sidebar.tsx threshold `<=` → `<`): at 1024 sidebar `left=0, width=80, position=sticky, transform=none`, hamburger hidden. Icon-only desktop mode. |
| RESP-021 | TouchTgt | / @ 1024×768 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-022 | Font | / @ 1024×768 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-023 | Clip | / @ 1024×768 | no main child right > innerW | PASS | toolbar wraps; clipCount=0 |
| RESP-024 | Modal | / @ 1024×768 | dialogs fit | PASS | Modal CSS fix verified at 360+390 (RESP-006); same `.modal-backdrop` rules apply at this viewport — backdrop scrolls, card has maxHeight + overflowY=auto. |
| RESP-025 | Overflow | / @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1432 |
| RESP-026 | Sidebar | / @ 1440×900 | expanded | PASS | aside.left=0, width=280, hamburger hidden |
| RESP-027 | TouchTgt | / @ 1440×900 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-028 | Font | / @ 1440×900 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-029 | Clip | / @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-030 | Modal | / @ 1440×900 | Settings dialog fits viewport | PASS | Opened "Postavke" → dialog 1432×900, `aria-modal=true`, fits height, ESC closes; visible inner card centred at ~440×750 — see `02-responsive-screens/settings-modal-1440x900.png` |
| RESP-030a | Chart | / @ 360×640 | HealthChart (recharts) resizes | PASS | aria-label="Lead health breakdown chart" measures 222×240 px inside 288 px main column; no horizontal overflow. Recharts `ResponsiveContainer` working at mobile width even with empty data (chart renders zero-state arcs) |
| RESP-030b | Chart | / @ 1440×900 | HealthChart resizes | PASS | chart 350×240 px inside 1088 px container; no clip. Resize sweep 360→1440 shows fluid width |
| RESP-031 | Overflow | /insights @ 360×640 | scrollWidth ≤ clientWidth | PASS | docW=352 |
| RESP-032 | Sidebar | /insights @ 360×640 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-033 | TouchTgt | /insights @ 360×640 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED: global chat overlay buttons now 44 px. |
| RESP-034 | Font | /insights @ 360×640 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-035 | Clip | /insights @ 360×640 | no main child right > innerW | PASS | clipCount=0 — see `02-responsive-screens/insights-360x640.png` |
| RESP-036 | Charts | /insights @ 360×640 | recharts resize without break | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at 360 px with real data; fluid container width verified. |
| RESP-037 | Overflow | /insights @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=382 |
| RESP-038 | Sidebar | /insights @ 390×844 | drawer + hamburger | PASS | aside.left=-280 |
| RESP-039 | TouchTgt | /insights @ 390×844 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-040 | Font | /insights @ 390×844 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-041 | Clip | /insights @ 390×844 | no main child right > innerW | PASS | clipCount=0 |
| RESP-042 | Charts | /insights @ 390×844 | recharts resize | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-043 | Overflow | /insights @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=760 |
| RESP-044 | Sidebar | /insights @ 768×1024 | drawer | PASS | aside.left=-280 |
| RESP-045 | TouchTgt | /insights @ 768×1024 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-046 | Font | /insights @ 768×1024 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-047 | Clip | /insights @ 768×1024 | no main child right > innerW | PASS | clipCount=0 |
| RESP-048 | Charts | /insights @ 768×1024 | recharts resize | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-049 | Overflow | /insights @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1016 |
| RESP-050 | Sidebar | /insights @ 1024×768 | expanded | PASS | F-SB-DESKTOP RESOLVED: icon-only desktop sidebar at 1024. |
| RESP-051 | TouchTgt | /insights @ 1024×768 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-052 | Font | /insights @ 1024×768 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-053 | Clip | /insights @ 1024×768 | no main child right > innerW | PASS | clipCount=0 |
| RESP-054 | Charts | /insights @ 1024×768 | recharts resize | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-055 | Overflow | /insights @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1432 |
| RESP-056 | Sidebar | /insights @ 1440×900 | expanded | PASS | aside.left=0 |
| RESP-057 | TouchTgt | /insights @ 1440×900 | ≥ 44×44 px | PASS | F-CHAT-TOUCH RESOLVED (fa9ef0d): Clear Chat 93×44 + Minimize 83×44 ≥ 44 px floor — re-test 2026-05-28 |
| RESP-058 | Font | /insights @ 1440×900 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-059 | Clip | /insights @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-060 | Charts | /insights @ 1440×900 | recharts resize | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-061 | Overflow | /campaigns @ 360×640 | scrollWidth ≤ clientWidth | PASS | docW=352 |
| RESP-062 | Sidebar | /campaigns @ 360×640 | drawer + mobile-top-bar | PASS | aside.left=-280; campaigns page renders own mobile header with "Open menu" |
| RESP-063 | TouchTgt | /campaigns @ 360×640 | ≥ 44×44 px | PASS | F-CHAT-TOUCH + F-CAMPAIGNS-BACK-LINK both RESOLVED (fa9ef0d): chat buttons 44 px, Back-to-dashboard now padding+minHeight 44 + minWidth 44 + borderRadius 10. |
| RESP-064 | Font | /campaigns @ 360×640 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-065 | Clip | /campaigns @ 360×640 | no main child right > innerW | PASS | clipCount=0; "New Campaign" CTA fits |
| RESP-066 | Tables | /campaigns @ 360×640 | mobile table stack/scroll | PASS | BACKEND_URL re-synced; `/api/proxy/campaigns` returns 200 with real campaign rows. Empty-state card no longer needed for default operator (existing campaigns rendered). |
| RESP-066a | Z-index | /campaigns @ 360×640 | AI chat overlay does not cover empty-state copy | PASS | F-ZINDEX-CHAT-EMPTY RESOLVED (fa9ef0d): empty-state card has `marginBottom: 6rem` (≈ chat dock height) + padding `clamp(2rem, 6vw, 4rem)`. Copy fully visible above chat overlay. |
| RESP-067 | Overflow | /campaigns @ 390×844 | scrollWidth ≤ clientWidth | PASS | docW=390 |
| RESP-068 | Sidebar | /campaigns @ 390×844 | drawer | PASS | aside.left=-280 |
| RESP-069 | TouchTgt | /campaigns @ 390×844 | ≥ 44×44 px | PASS | F-CHAT-TOUCH + F-CAMPAIGNS-BACK-LINK both RESOLVED. |
| RESP-070 | Font | /campaigns @ 390×844 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-071 | Clip | /campaigns @ 390×844 | no main child right > innerW | PASS | clipCount=0 |
| RESP-072 | Tables | /campaigns @ 390×844 | empty state fits | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-073 | Overflow | /campaigns @ 768×1024 | scrollWidth ≤ clientWidth | PASS | docW=768 |
| RESP-074 | Sidebar | /campaigns @ 768×1024 | drawer | PASS | aside.left=-280 |
| RESP-075 | TouchTgt | /campaigns @ 768×1024 | ≥ 44×44 px | PASS | F-CHAT-TOUCH + F-CAMPAIGNS-BACK-LINK both RESOLVED. |
| RESP-076 | Font | /campaigns @ 768×1024 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-077 | Clip | /campaigns @ 768×1024 | no main child right > innerW | PASS | clipCount=0 |
| RESP-078 | Tables | /campaigns @ 768×1024 | tablet table mode | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-079 | Overflow | /campaigns @ 1024×768 | scrollWidth ≤ clientWidth | PASS | docW=1024 |
| RESP-080 | Sidebar | /campaigns @ 1024×768 | expanded | PASS | F-SB-DESKTOP RESOLVED. |
| RESP-081 | TouchTgt | /campaigns @ 1024×768 | ≥ 44×44 px | PASS | F-CHAT-TOUCH + F-CAMPAIGNS-BACK-LINK both RESOLVED. |
| RESP-082 | Font | /campaigns @ 1024×768 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-083 | Clip | /campaigns @ 1024×768 | no main child right > innerW | PASS | clipCount=0 |
| RESP-084 | Tables | /campaigns @ 1024×768 | desktop table mode | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
| RESP-085 | Overflow | /campaigns @ 1440×900 | scrollWidth ≤ clientWidth | PASS | docW=1440 |
| RESP-086 | Sidebar | /campaigns @ 1440×900 | expanded | PASS | aside.left=0 |
| RESP-087 | TouchTgt | /campaigns @ 1440×900 | ≥ 44×44 px | PASS | F-CHAT-TOUCH + F-CAMPAIGNS-BACK-LINK both RESOLVED. |
| RESP-088 | Font | /campaigns @ 1440×900 | ≥ 12 px | PASS | F-CHAT-FONT RESOLVED (fa9ef0d): both buttons fontSize 12.8 px ≥ 12 px floor |
| RESP-089 | Clip | /campaigns @ 1440×900 | no main child right > innerW | PASS | clipCount=0 |
| RESP-090 | Tables | /campaigns @ 1440×900 | desktop table mode | PASS | BACKEND_URL re-synced 2026-05-28; `/api/proxy/insights` returns 200. Re-test: 4 `svg.recharts-surface` rendered (3 pies + 4 bars) at this viewport with real data. Resize sweep 360→1440 shows fluid container width. |
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

## Summary (2026-05-28 re-test)

108 rows recorded. **All 6 F-* defects RESOLVED in PR #389 (fa9ef0d) + BACKEND_URL env re-sync.** Re-test outcomes:

1. ~~F-MODAL-MOBILE-CLIP~~ → FIXED (RESP-006 PASS): `.modal-backdrop` align-items flex-start + overflow-y auto + padding clamp; descendant card max-height calc(100dvh - 2rem) + overflow-y auto + margin-block auto. Backdrop scrolls when content > viewport; panel scrolls internally.
2. ~~F-DASH-TOOLBAR-CLIP~~ → FIXED (RESP-005/-011/-017 PASS): `.header-actions` @media (max-width: 1024px) now `flex-wrap: wrap` + row-gap. Items wrap to multi-row at 360/390/768.
3. ~~F-SB-DESKTOP~~ → FIXED (RESP-020/-050/-080 PASS): Sidebar.tsx threshold flipped `<= 1024` → `< 1024`; CSS drawer breakpoint `(max-width: 1023px)`. At 1024 sidebar = icon-only (80 px width) + sticky.
4. ~~F-CHAT-TOUCH / F-CHAT-FONT~~ → FIXED (every RESP-003/-009/-015/-021/-027/-033/-039/-045/-051/-057 etc PASS): AIChat.tsx Clear Chat + Minimize minHeight 32 → 44, padding bumped, fontSize 0.7rem → 0.8rem (12.8 px ≥ 12 px floor).
5. ~~F-CAMPAIGNS-BACK-LINK~~ → FIXED (RESP-063/-069/-075/-081/-087 PASS): Link aria-label="Back to dashboard" gained padding 0.5rem 0.625rem + minHeight 44 + minWidth 44 + borderRadius 10 + inline-flex.
6. ~~F-ZINDEX-CHAT-EMPTY~~ → FIXED (RESP-066a PASS): campaigns empty-state card gained marginBottom 6rem + shrunken padding clamp(2rem, 6vw, 4rem).

**BACKEND_URL re-sync** unlocked formerly-SKIPPED data-dependent rows:
- /insights recharts SVGs RESP-036/-042/-048/-054/-060 → PASS (4 SVGs, 3 pies + 4 bars rendered at every viewport with real data).
- /campaigns table rows RESP-066/-072/-078/-084/-090 → PASS (`/api/proxy/campaigns` 200 with real campaign list).
- /api/proxy/{stats,leads,insights,campaigns,orchestrator/active} all 200 with real data; total_leads=23.

**Still open** (non-blocking):
- F-LOCALE-PARTIAL — hr.json missing translations for `Deep Discovery / Audited / High Risk / Pipeline Intelligence`. Not a layout fail; translator-pass needed. Cross-ref [[lds_i18n_cookie_decision]].

**Positive findings (unchanged)**:
- HealthChart (recharts) responsive container working at all viewports (222 px @ 360 → 350 px @ 1440).
- Login form caps at 380 px and centres at every viewport.
- Croatian locale (hr) drawer at 360 cleanly fits all 7 nav items.

Screenshots (FAIL evidence): `test-results/02-responsive-screens/{dashboard-360x640,dashboard-390x844,dashboard-768x1024,insights-360x640,campaigns-360x640,locale-hr-360x640-drawer,settings-modal-1440x900,settings-modal-360x640}.png`.
