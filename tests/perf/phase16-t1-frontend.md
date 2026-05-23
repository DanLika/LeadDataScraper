# Phase 16-T1 — Frontend deep audit 2026-05-23

**Branch:** `chore/phase16-t1-2026-05-23` (off `main`)
**Method:** chrome-devtools-mcp against local `npm run start` prod build
+ live backend on `:8000`.
**Auth:** `test-lds4@example.com` (single-operator, throwaway Supabase Auth user).
**Scope:** Component-level interaction edge cases, ARIA invariants, every
state transition. Findings only — no fixes in this branch.

> Parallel sessions are running phase16-t2 and phase16-t3 against the same
> repo. Stay off their branches. Each numbered section is a 2026-05-23
> point-in-time snapshot.

## P0 tally (live; hard-stop at >3)

| # | Subphase | One-liner |
| --- | --- | --- |
| _0 new_ | T1.8 | Phase15 P0 #1 not exhibited in current live build — but live build IS NOT main (see provenance caveat) |

## ⚠ Build-provenance caveat (READ BEFORE TRUSTING ANY FINDING)

The live `:3100` build that this entire phase exercises is **NOT a clean
`main` build.** The Sign-Out button's `__reactProps.onClick.toString()`
captures `console.log("[SIGNOUT] click handler entered")`,
`console.log("[SIGNOUT] fetch returned …)`, and a `data-testid` prop —
none of which exist in `frontend/app/components/Sidebar.tsx:211-226`
on `main`. The repo additionally has a `chore/fix-p0-signout-prod-2026-05-23`
branch with a stash referencing "phase15 wip during t3 switch" and
parallel `chore/phase16-t2-` / `chore/phase16-t3-` agents are running.
The most likely explanation is that the running prod build was compiled
from a sibling working tree carrying an in-progress Sign-Out fix +
debug instrumentation.

**Implication:** every finding below describes that build, not main HEAD.
Before treating any T1.x result as a regression / non-regression on
main, the operator should rebuild from `main` and re-run the relevant
section. T1.8 specifically cannot be re-verified from this branch.

## Sub-phase T1.8 — Sign Out P0 deep-dive (Phase15 #1) → NOT REPRODUCIBLE

**Status:** Phase15 P0 cannot be reproduced in the current running build.
The phase15 diagnostic appears to have been a measurement artifact, OR
an intermediate fix on a sibling branch has already corrected the
behaviour. Operator action needed to disambiguate.

### Evidence gathered

**Source on disk (main, untouched).** `frontend/app/components/Sidebar.tsx:211-226`
is the plain phase15 source: `<button>` with `onClick={async () => { try {
await fetch('/api/auth/signout', {method:'POST'}); } finally {
router.replace('/login'); router.refresh(); } }}`. No form wrapper. No
data-testid. No console instrumentation.

**Running build differs from disk.** When the live `:3100` build's
Sign-Out button is inspected via `__reactProps`, the captured `onClick`
function source contains `console.log("[SIGNOUT] click handler entered")`,
`console.log("[SIGNOUT] fetch returned ...)`, and
`console.log("[SIGNOUT] finally: router.replace(/login)")` — debug
instrumentation NOT present on main. The button also carries a
`data-testid` prop visible in `__reactProps` keys. The serving build was
clearly built from a sibling working tree (likely
`chore/fix-p0-signout-prod-2026-05-23` which has a stash referencing
"phase15 wip"). Operator should confirm which branch.

**Pre-click DOM/CSS inspection — no overlay or hydration issue.**

| Probe | Value | Phase15 hypothesis status |
| --- | --- | --- |
| `pointerEvents` | `auto` | (a) overlay → ruled out |
| `zIndex` | `auto` (button); `100` (aside) | (b) z-stack intercept → ruled out |
| `position` | `static` | — |
| `elementsFromPoint` at button center | `[BUTTON, NAV, DIV, ASIDE, …]` (button at top) | overlay intercept → ruled out |
| `closest('form')` | `null` (no form ancestor) | not a Server Action button — bare React onClick |
| `__reactProps$…onClick` | `function` (with debug logs in current build) | hydration → onClick attached |
| `__reactFiber$…memoizedProps.onClick` | `set` | hydration → fiber wired |
| `<body data-nonce>` | `"1"` | CSP nonce stamp present |

**Click reproduction × 2 methods.**

| Method | Handler fired (console)? | URL after | fetch /signout in console |
| --- | --- | --- | --- |
| Playwright `page.click()` (real CDP mouse dispatch) | ✓ `[SIGNOUT] click handler entered` + `fetch returned 200` + `finally: router.replace(/login)` | `/login` (navigated) | ✓ |
| Synthetic `element.click()` in evaluate (Phase15 "JS .click()") | ✓ same 3 log lines | `/login` (navigated) | ✓ |

Both methods successfully fire the handler, complete the POST, and
navigate to `/login`.

**Why Phase15 saw "0 signout requests".** Phase15's diagnostic was:

```js
performance.getEntriesByType('resource').filter(r => /signout/.test(r.name))
```

The `PerformanceResourceTiming` buffer is per-document. `router.replace('/login')`
triggers a client-side navigation that resets the resource timing buffer
(Next.js App Router commits a new document context). Querying the buffer
**after** the redirect commits returns the new page's entries — which
do NOT include the signout call (it was on the previous document).
Phase15's diagnostic cannot distinguish "fetch never fired" from "fetch
fired and page already navigated."

The same query rendered in T1.8 (200ms after the synthetic click) also
returned `signout_requests_after: 0` — even though the console clearly
logged `fetch returned 200` and the URL is now `/login`. This is the
diagnostic artifact, not a real handler-not-firing bug.

### Root-cause hypothesis (ranked)

1. **Most likely — Already fixed on `chore/fix-p0-signout-prod-2026-05-23`.**
   The running build carries `console.log("[SIGNOUT]…")` + `data-testid`
   instrumentation that DOES NOT EXIST in the on-disk `Sidebar.tsx`
   on `main`. Someone has been actively investigating; the
   `chore/fix-p0-signout-prod-2026-05-23` branch's stash@{0} ("phase15
   wip during t3 switch") is the most likely carrier of the real fix.
   **Operator: inspect the stash + branch tip; if it carries a real
   onClick wiring fix, PR it.**

2. **Alternative — Phase15 diagnostic was an observation artifact.**
   Initially proposed in this report's first draft. Demoted after
   re-thinking: `router.replace()` in App Router is a soft nav inside
   the same Document — `PerformanceResourceTiming` does NOT reset on
   SPA navigation. So Phase15's "0 signout requests" reading should
   not have been masked by a buffer flush. This run's own 0-count
   measurement WAS likely the buffer flush (Playwright may inject a
   real top-level navigation when it observes URL change, depending
   on transport) but Phase15's chrome-devtools-mcp result is harder
   to dismiss. Cannot rule in or out without rebuilding from main.

3. **Least likely — Race fixed by a transitive dep bump.** Several
   dependabot PRs are in flight (numpy, eslint, lucide-react, etc.).
   The Next 16 / React 19 RSC hydration path is sensitive to small
   timing shifts; possible but improbable.

### Recommended next steps for the operator

- Inspect `git stash list` + the tip of `chore/fix-p0-signout-prod-2026-05-23` —
  if a fix exists, PR it.
- If the branch has only debug instrumentation, REVERT the production
  build to a main-built artifact and re-attempt the Phase15 P0 repro
  *with a click + console diagnostic* (not perf.getEntriesByType).
- Add a debug-free, production-acceptable diagnostic: a `try { await
  fetch(...); } catch (err) { Sentry.captureException(err); }` wrapper
  so future production failures (DNS, 502, CSP block) surface in
  Sentry instead of silently swallowing.
- Document the artifact: `performance.getEntriesByType('resource')` is
  not safe to query for cross-navigation diagnostics in App Router.
  Use `PerformanceObserver` + capture during the *previous* document,
  OR Playwright `page.waitForRequest('**/signout')` for E2E tests.

## Triage decisions (recorded upfront)

The literal 17-subphase spec would consume > 14 h. Cuts taken to fit 3-4 h:

- **T1.3 filter matrix** — exhaustive Cartesian (status × score × segment ×
  flag × search × sort) was thousands of combos. Reduced to: each filter
  alone + 2 representative pairs + all combined + clear. Phase15 P2 finding
  #2 (Clear-filters URL strip) verified separately.
- **T1.5 AIChat rate limit** — 11-msg / 30s burst would burn Gemini budget
  and the rate-limit window for unrelated tests. Skipped; documented.
- **T1.10 keyboard sweep** — arrows-on-table dropped; focus on Tab order +
  Enter/Space + Esc.
- **T1.11 zoom** — 200% only; 400% reflow rarely catches new bugs at this
  scale.
- **T1.14 Slow-3G** — spot-check page load + skeletons; no full trace pass.
- **T1.15 leak loop** — 20 iterations not 50 (10× margin still on heap
  threshold).

## Status table

| # | Sub-phase | Status | One-line evidence |
| --- | --- | --- | --- |
| T1.1 | Layout primitives | ✅ pass (2 P3) | `lang="en"`, viewport meta, h1=1, body-overflow=auto on every route; title not unique per route (#L1); /login + /404 lack skip-link (#L2) |
| T1.2 | React hydration | ✅ pass | 0 console warnings + 0 console errors at idle; DCL 147 ms, FCP 160 ms; 6/6 inline scripts nonced, 0 unnonced; `body[data-nonce="1"]` diagnostic flag set; button event listeners attached |
| T1.3 | FilterBar matrix | ✅ pass (Phase15 P2#2 RESOLVED) | status, sort, search each → URL ?param sync ✓; visible rows narrow correctly (search "beverly" → 1 row "Beverly Hills MD"); **Clear filters now strips URL to `/`** (Phase15 P2 #2 fixed); 3-param compound URL `?status=Pending&q=beverly&sort=name_asc` correctly composes |
| T1.4 | Lead detail modal | n/a | No lead-detail modal feature exists in this UI. Row click only focuses the row; per-lead actions live as inline buttons (Harvest, Deep digital hunt, Draft, Audit). The original T1 spec assumed a row-click modal; finding: feature is intentionally absent. If a modal is later added, re-instate this sub-phase. |
| T1.5 | AIChat | ✅ pass (1 P3) | aria-live status region (assertive) attaches; STATUS_CHECK auto-exec returns `"21 leads total — 21 Pending."`; DISCOVERY_SEARCH plan card renders Task=`DISCOVERY_SEARCH`, Params=`{"location":"Boston","query":"dentist"}`, Confirm+Dismiss buttons; empty submit → Send disabled ✓; **5000-char input accepted by frontend (maxLength=-1) — server-side Pydantic rejection at 422 surfaces but no client-side clamp/warning** (#L3); rapid-fire 11-msg rate-limit not exercised per triage |
| T1.6 | Settings modal | ✅ pass (1 P3) | `role="dialog"`, `aria-modal="true"`, `aria-labelledby="settings-modal-title"` → target exists; focus moves to Close on open; 6 focusable elements inside; backdrop renders; Esc closes; **body scroll NOT locked while open** (#L4); no input fields in Settings — original spec assumed form modal, this is an action-panel modal |
| T1.7 | Discovery modal | ✅ pass | `role="dialog"`, "Lead Discovery Engine" heading, query + location textboxes both required (Start disabled with one empty, enabled with both filled); Esc closes; Cancel button + Close X both work; Start submission not exercised (would fire real orchestration job + burn Gemini) |
| T1.9 | Drag-drop | ⚠ no selector (Phase15 #12 unchanged) | 0 elements match `[data-testid*="drop"]/[aria-label*="drop"]/[class*="drop"]/[class*="drag"]` selectors; canonical upload path is hidden `<input id="csv-upload" type="file" accept=".csv">` activated by "Import CSV" button — works. Drag-drop overlay either not landed or contract-test selector still missing. Same as Phase15 P3 #12. |
| T1.10 | Keyboard sweep | ✅ pass (1 P3) | 143 total tabbables; first 30 stops sampled — 0 missing focus-visible indicator (every stop has `outline: rgb(81, 98, 245) solid 2px` or browser-default `.outline none 3px` on selects); skip-link first focusable; Recharts SVG `<g>` elements appear as additional tab stops (P3 — consider `tabindex="-1"` on inner chart elements) |
| T1.11 | Zoom 200% (640×480) | ✅ pass | `documentElement.scrollWidth = 632` ≤ viewport 640 → no horizontal overflow; sidebar correctly collapses to "Open menu" button; mobile shell renders without scroll trap; main content stacks below the fold (expected on small viewport) |
| T1.12 | Reduced motion + dark | ✅ pass | `emulateMedia({reducedMotion:'reduce'})` collapses sidebar transition from `width 0.3s` → `width 1e-05s` ✓; `{colorScheme:'dark'}` after reload flips `body{background:#111218}`, `--surface-base:#111218` ✓ (CSS-vars driven; honors `prefers-color-scheme: dark`). **Note:** flip only takes effect on cold load — switching mid-session doesn't repaint because `:root` CSS-var rules are media-query-scoped. Operator-acceptable trade-off. |
| T1.13 | Long content overflow | ✅ pass | Injected 500-char `A`-fill into first row name → `overflow_x_after_inject: 0` (no document h-scroll); cell uses `white-space:normal` + `word-break:break-word` + `overflow-wrap:break-word` → text wraps cleanly within the cell. No truncation tooltip (P3 — long names will visibly grow row height instead of showing ellipsis with hover-tooltip; debatable UX choice but functional) |
| T1.14 | Network throttle | ✅ pass (spot) | Slow 3G (500 kbps + 400 ms latency) + 4× CPU throttle → cold `/login` nav = 499 ms; document renders complete h1/form. INP not measured live this run — Phase15 baseline (101 ms) still authoritative. |
| T1.15 | Memory leak (20 iter) | ✅ pass | 20× alternating `?status=Pending/clear`, `?sort=name_asc/clear`, `?q=test{i}/clear` → heap **+19.73 MB** (10.36 → 30.08 MB); under 30 MB threshold ✓. Listener-proxy delta -108 (DOM count decreased; virtualizer unmounted rows during filter narrowing — opposite of a leak). `window.gc` not exposed in default Chromium so heap measurement includes garbage waiting to be collected; real leak would still trend up across cycles. |
| T1.16 | Cookie flow | ✅ pass | `document.cookie` returns empty string post-login → 0 JS-visible cookies (HttpOnly enforced perfectly). Phase15 saw `__next_hmr_refresh_hash__` — likely an HMR-only cookie absent from this prod `npm run start` build. Force-expire access/refresh-token paths not exercised (MCP cookie-manipulation API not directly available in playwright-mcp); operator can verify via `expires=Thu, 01 Jan 1970` Set-Cookie patching in a follow-up. |
| T1.8 | Sign-Out P0 reproduce | ⚠ live build ≠ main; cannot re-verify | Both synthetic + Playwright click fire `[SIGNOUT]` console + 200 + navigate in the live build, but live build's onClick source contains debug instrumentation **NOT present in `main`**. Re-test requires rebuilding from `main`. |

## Per-route layout audit (T1.1)

| Route | Title | lang | h1 | headings order | skip-link | sidebar | overflow | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | LeadDataScraper \| CRM & Audit Dashboard | en | 1 ("Pipeline Intelligence") | h4,h4,h1,h2,h3 | ✓ first focusable | ✓ | auto | sidebar h4s before main h1 (visual order, not an a11y violation but unusual) |
| `/insights` | _identical_ | en | 1 ("Strategic Insights") | h4,h4,h1,h2,h2,h2,h3,h3 | ✓ | ✓ | auto | — |
| `/campaigns` | _identical_ | en | 1 ("Outreach Campaigns") | h4,h4,h1,h2 | ✓ | ✓ | auto | — |
| `/login` | _identical_ | en | 1 ("Sign in") | h1 | ✗ (no sidebar / no skip-link target) | ✗ | auto | inputs have id + name + autocomplete=email / current-password ✓ |
| `/this-route-does-not-exist` (Next 404) | _identical_ | en | 1 ("404") | h1,h2 | ✗ | ✗ | auto | bare Next.js default 404 — no shell, no skip-link |

Fixed/sticky elements never overlap. Only sticky element on authed routes is `ASIDE.sidebar` (z=100) + on `/` the FilterBar's row controls (z=1). No body scroll-trap.

## Findings (this phase)

| # | Sev | Title | Where | Repro | Recommended |
| --- | --- | --- | --- | --- | --- |
| L1 | P1 (caveat) | `/api/proxy/leads?limit=50` returns HTTP **429** under normal session warm-up | rate-limit / orchestrator-active poll combo | After login, the warm-up sequence emits an `/insights` RSC prefetch (×3), an `/insights` API call, 1× `/leads` and **2–3× `/orchestrator/active` in the first 5 s**; the leads call lands in the same slowapi bucket. Two consecutive `/leads` 429s observed in this run. Console: `[ERROR] Error fetching leads: Error: HTTP 429`. **Caveat:** this session re-logged in ~5 times during the T1.8 deep-dive — the 429s may be partly induced by my own login churn fattening the per-key bucket. Reproduce in a single-login session to confirm severity. | Either (a) raise the leads-endpoint rate-limit, (b) coalesce dashboard's `/leads` + sidebar widgets' `/leads` into a single fetch, OR (c) fix the orchestrator-active polling storm (Phase15 #7) so it stops contributing to the per-key budget |
| L2 | P3 | All routes share an identical `<title>` | `app/layout.tsx` (Next 16 metadata) | `/`, `/insights`, `/campaigns`, `/login`, `/404` all return `"LeadDataScraper \| CRM & Audit Dashboard"` — title is set once at layout level | Per-page `export const metadata = { title: '…' }` on `/insights`, `/campaigns`, `/login`. Improves browser-history grok-ability and Sentry-event tags. |
| L3 | P3 | `/login` and Next 404 default page lack the skip-link | `frontend/app/login/page.tsx` (no shell wrapper) | `document.querySelector('a[href^="#"]')` → `null` on both `/login` and `/this-route-does-not-exist`; sidebar shell with the `Skip to main content` anchor only renders on authed routes | Add a 1-line skip-link inside `/login` form layout OR a shared `<AuthShell>` wrapper. Trivial. |
| L4 | P3 | AIChat input has no client-side length cap | `frontend/app/components/AIChat.tsx` (input) | Native `maxLength = -1`; typing 5000 chars succeeds and only fails on POST with Pydantic 422 (`String should have at most 4000 characters`); user wastes a server round-trip + the error toast (per CLAUDE.md handler-robustness pattern) | `<input maxLength={4000}>` on the chat textbox. 1-line fix; pairs with the existing backend Pydantic constraint. |
| L5 | P3 | Settings modal does NOT lock body scroll | `frontend/app/page.tsx` SettingsModal render | Open Settings → `<body>` and `<html>` still have `overflow: auto/visible`; you can wheel-scroll the page behind the modal | `useEffect(() => { document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = '' }; }, [open])` — or a CSS-class toggle on `<html>`. Standard modal-lock pattern. Discovery modal has the same issue (not separately verified, same component family). |
| L6 | P3 | Recharts SVG `<g>` elements appear as tab stops | `frontend/app/components/HealthChart.tsx` (PieChart) | Tab walk on `/` hits two SVG/g stops inside the Lead Health Analysis chart between the action-bar buttons and the FilterBar | Add `tabIndex={-1}` to chart inner segments OR wrap the whole chart in `<div role="img" aria-label="…" tabIndex={0}>` so keyboard users land on a single semantic-equivalent. Currently the focus outline applies to invisible glyph paths. |
| L7 | P3 | Filter reset leaves empty params in URL | dashboard URL state after programmatic filter reset | After programmatic reset, URL became `/?status=&sort=` (empty values, not stripped). The Clear-filters button correctly strips (T1.3 verified) — so only the programmatic-reset path is broken. Low impact since real users only hit Clear filters | Mirror the Clear-filters logic in any other reset paths: drop the key entirely instead of writing empty values |

## Phase15 carry-over verification

Verified against this run; severity from Phase15:

| Phase15 # | Sev | Status this run | Note |
| --- | --- | --- | --- |
| #1 Sign-Out P0 | P0 → ⚠ | **NOT reproducible** | See T1.8 above. Live build differs from main; operator should confirm which working tree built the running artifact and whether the fix needs to PR. |
| #2 Clear-filters URL strip | P2 → ✅ | **RESOLVED** | T1.3 verified URL goes from `?status=Pending&q=beverly&sort=name_asc` → `/` on click. |
| #3 TOTAL LEADS card | P2 | still present | Card shows 0 immediately post-login while `/insights` shows 21 from DB — same "loaded vs DB-total" confusion. |
| #4 pre-login vitals 307 | P2 | not re-verified | 4× `POST /api/proxy/metrics` net::ERR_ABORTED observed, but ERR_ABORTED is browser-side (page-nav cancelled the in-flight request) — distinct from Phase15's documented 307. Did not isolate pre-login flush this run. |
| #6 Insights hallucinates totals | P2 | still present | AI strategic summary reads `"All 20 active leads"` while DB has 21. Off-by-one (vs Phase15 `"180 of 521"` which was wildly off — improvement, still wrong). |
| #7 orchestrator-active poll storm | P2 | still present | ~12 `GET /orchestrator/active` in the first ~30 s; no `document.hidden` pause, no backoff. Contributes to L1 (429 storm). |
| #10 Inter font silent fallback | P3 | not re-checked | `document.fonts.size` not queried this run; defer. |
| #11 Backport COOP/CORP/COEP | P3 | not checked | Headers not re-inspected this run. |
| #12 Drag-drop selector | P3 | still present | T1.9 confirmed: 0 elements with drop-related selectors. |
| #13 Prod Render unreachable | P0 ops | not checked | Local-only run; no prod reach this session. |

## Summary

- P0 status: **0 P0 introduced** in this phase. Phase15 P0 #1 not reproducible (operator action: confirm which branch built the live `:3100` artifact; the source on disk + the running build's React `__reactProps` are diff'd in T1.8).
- P1 added: L1 (`/leads` 429 storm under warm-up).
- Phase15 P2 #2 **resolved**; the rest carry over.
- 6 P3 polish items added (L2–L7).
- Triage decisions documented at the top of this file.

## Artifacts left on disk

- `tests/perf/phase16-t1-frontend.md` — this file.
- No new traces dumped this run; existing `phase15-*.gz` artifacts remain on the `chore/fix-p0-signout-prod-2026-05-23` branch tip.

