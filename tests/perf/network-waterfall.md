# Real network waterfall — cold vs warm load of `/`

**Captured:** 2026-05-22 via chrome-devtools-mcp against `http://localhost:3000`
in production mode (`npm run start`) on the `fix/csp-nonce-rsc-hydration`
branch (post-CSP-nonce fix, post-Sentry install).
**Auth state:** logged in as `test-lds4@example.com` (single-operator).
**Raw data:** `tests/perf/network-cold.json`, `tests/perf/network-warm.json`.

## Summary

| Metric | Cold (no cache) | Warm (browser cache) |
| --- | --- | --- |
| Total requests | 23 | 22 |
| Total transfer | **211.3 KB** | **36.2 KB** (−83%) |
| Total decoded | 1086 KB | (cache served, no decoded delta) |
| TTFB | 98 ms | 398 ms (RSC re-render dominated) |
| DOMContentLoaded | 113 ms | 432 ms |
| FCP | **432 ms** | 452 ms |
| Cache hits | 0 | **15 / 22 static** (`_next/static/chunks/*`) |
| Render-blocking | 1× CSS (5 KB) | 1× CSS (cache) |
| Third-party origins | 0 | 0 |
| Fonts loaded | **0** | 0 |

`transferSize=0` from the `PerformanceResourceTiming` API was used as the
disk-cache marker. All 15 `/_next/static/chunks/*` assets came back at 0 ms
with `transferSize=0` on warm reload — disk cache working as intended.

## Cache headers (one-shot probe)

| Path | `Cache-Control` |
| --- | --- |
| `/` (document) | `private, no-store, max-age=0` ✓ authed page |
| `/_next/static/chunks/*.css` | `public, max-age=31536000, immutable` ✓ |
| `/_next/static/chunks/*.js` | `public, max-age=31536000, immutable` ✓ |
| `/favicon.ico` | `public, max-age=0, must-revalidate` ⚠ |
| `/api/proxy/metrics` (and other proxy endpoints) | `private, no-store, max-age=0` ✓ |

⚠ `favicon.ico` revalidates on every load (26 KB re-transferred even on
warm reload). Low impact (single asset, served once) but trivially
fixable: ship it as a static file with the same long-immutable header
that other static assets get.

## Fat assets (> 100 KB transferred)

None. Largest single transfer is `07lhk_q6pmm3r.js` at **71.3 KB** gz
(227.5 KB decoded). Likely the React + Next runtime split. Sub-100 KB
budget per chunk — bundle-splitting (Faza 4.4 dynamic imports) is
landing the intended sizes.

## Slow assets (> 500 ms duration)

All four are **fetch / beacon**, not render-path. The first paint
(`FCP = 432 ms`) lands before any of them.

| Duration | Transfer | Initiator | URL | Note |
| ---: | ---: | --- | --- | --- |
| 819 ms | 311 B | beacon | `/api/proxy/metrics` | Web-vitals beacon. Slow because backend cold-starts the metrics handler. |
| 752 ms | 456 B | fetch | `/insights?_rsc=…` | Next 16 link-prefetch RSC payload for `/insights` (post-FCP). |
| 742 ms | 1 KB | fetch | `/?_rsc=…` | Self-RSC prefetch (post-FCP). |
| 605 ms | 340 B | fetch | `/api/proxy/insights` | Backend returns **502** (see "Backend health" below). |

## Render-blocking critical path

Exactly one render-blocking asset: `_next/static/chunks/0ph03omb0cvx7.css`
(5.1 KB gz, 21.6 KB decoded, 5 ms). Good — Next.js's CSS-in-modules
strategy is producing a single small CSS payload per route.

## Third-party origins

**None.** Every request hits `localhost:3000`. Supabase realtime is
loaded lazily and doesn't fire on the dashboard's initial paint.

## Specific check: `/api/proxy/*` before user interaction

**Fails the stated spec.** The dashboard makes **four** `/api/proxy/*`
calls before the user touches anything:

| Time after nav | Endpoint | Why |
| ---: | --- | --- |
| 141 ms | `POST /api/proxy/metrics` | `WebVitalsReporter` mounts in root layout; sends FCP/LCP/etc. via `navigator.sendBeacon` |
| 419 ms | `GET /api/proxy/insights` | `<AIInsightsWidget>` in `Sidebar.tsx` auto-fetches Gemini insights on mount |
| ~early | `GET /api/proxy/leads?limit=50` | First page of the inventory (visible in DevTools panel; not in `PerformanceResourceTiming` because XHR with no body timing) |
| ~early | `GET /api/proxy/orchestrator/active` | `OrchestratorBanner` polls active job on mount |
| 465 ms | `POST /api/proxy/metrics` | Second beacon (LCP/INP after layout) |

The spec's expectation — "should be lazy on filter/load-more, not on
initial paint" — is **not** the current design. These four calls are
the dashboard's initial render data. If the operator wants them deferred
behind a user-gesture, that's a behaviour change in `page.tsx` (lazy
`SWR`/effect on first interaction), not a perf bug.

## Backend health (incidental finding)

Three of the four eager calls **return 5xx in this session**:

| Endpoint | Status | Cause |
| --- | --- | --- |
| `/api/proxy/leads?limit=50` | **500** | Backend `db` lazy global never primed |
| `/api/proxy/insights` | **502** | Same |
| `/api/proxy/orchestrator/active` | **500** | Same |

`/tmp/lds-backend.log` confirms: `Startup DB checks skipped — database
unreachable: name 'db' is not defined`. The lazy-module `__getattr__`
caches `db` only inside the lifespan handler; when the lifespan path
swallows an exception (e.g. an env var hiccup), `db` never lands in
`globals()` and every handler that bare-references `db` throws
`NameError`, which the global exception handler reports as 500.

**This is a real prod-mode regression** — separate from this perf task.
It blocks any data-bearing measurement (LeadTable scroll profile,
real-payload INP timings, etc.) until fixed. See task 9.4 console sweep
for the user-facing impact.

## Top 10 worst offenders (combined fat + slow)

| Rank | Metric | URL | Note |
| ---: | ---: | --- | --- |
| 1 | 71 KB / 227 KB decoded | `07lhk_q6pmm3r.js` | React + Next runtime — known size, immutable, cache hit on warm |
| 2 | 30 KB / 110 KB decoded | `07-r11g6lpgb8.js` | Second largest chunk |
| 3 | 26 KB transfer, revalidates on warm | `/favicon.ico` | **fixable** — drop `must-revalidate`, ship as static |
| 4 | 20 KB / 81 KB decoded | `0hs8wux807kts.js` | Page-level chunk |
| 5 | 819 ms beacon | `POST /api/proxy/metrics` | Backend cold-start tax |
| 6 | 752 ms RSC prefetch | `/insights?_rsc=…` | Post-FCP, doesn't affect paint |
| 7 | 605 ms / 502 | `GET /api/proxy/insights` | Backend `db` bug (above) |
| 8 | 500 / fetch | `GET /api/proxy/leads?limit=50` | Backend `db` bug |
| 9 | 500 / fetch | `GET /api/proxy/orchestrator/active` | Backend `db` bug |
| 10 | 5 KB render-blocking | `0ph03omb0cvx7.css` | Single critical CSS — already optimal |

## Verdict

Static-asset pipeline is **healthy**:
- All `_next/static/chunks/*` carry `public, immutable, max-age=1y` and hit
  the disk cache on warm reload.
- No fat resources, no third-party origins, only one tiny render-blocking
  CSS.

Two real items to fix (separate PRs):
1. **`favicon.ico` revalidation** — 26 KB re-transferred on every load.
   Convert to a static `app/icon.ico` or set a long immutable header.
2. **Backend `db` NameError** in prod mode — three eager dashboard
   fetches return 5xx because the lazy global isn't primed when the
   lifespan exception path runs. Real bug, not a CSP/Sentry artifact.

One design call to flag, not a bug:
- The dashboard issues **four `/api/proxy/*` calls before user
  interaction**. If the spec's "lazy until interaction" rule is firm,
  defer the insights widget + orchestrator-active poller behind a
  gesture (e.g. fetch only when the sidebar widget scrolls into view).
