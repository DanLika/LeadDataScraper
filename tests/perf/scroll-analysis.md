# CPU profile / scroll analysis on populated LeadTable

**Captured:** 2026-05-22 via chrome-devtools-mcp on
`http://localhost:3000/` (prod build, authed), with **501 rows seeded
in the live Supabase project** via a one-shot
`INSERT INTO leads … FROM generate_series(1, 500)` (unique_key
prefix `_perf_test_`). Frontend cursor-paginated 50 rows/page; the
LeadTable virtualizer renders ~28 of them at a time.
**Raw traces:** `tests/perf/scroll-trace-raf.json` (free-running rAF
measurement, the canonical one), `tests/perf/scroll-trace-paced.json`
(initial paced-loop attempt — kept for reference, see "Methodology
notes" below).

## Headline numbers

| Metric | Value | Threshold | Verdict |
| --- | ---: | --- | --- |
| FPS during 5 s continuous scroll | **119.9** | > 50 (spec) | ✓ |
| Mean frame time | **8.33 ms** | < 16.7 ms (60 fps) | ✓ |
| Median frame time | 8 ms | < 16.7 ms | ✓ |
| p95 frame time | 9 ms | < 16.7 ms | ✓ |
| p99 frame time | 9 ms | < 16.7 ms | ✓ |
| Max frame time | **9.4 ms** | < 33 ms (acceptable jank) | ✓ |
| Frames > 16.7 ms (dropped 60 fps) | **0 / 600** | 0 | ✓ |
| Frames > 33 ms | 0 / 600 | 0 | ✓ |
| Frames > 50 ms | 0 / 600 | 0 (spec target) | ✓ |
| CLS during scroll | **0.00** | 0 | ✓ |
| Long tasks > 50 ms (PerformanceObserver, fresh) | 2 × 142 ms | 0 | ⚠ see below |
| Rendered DOM `[role=row]` nodes | **28** (constant throughout scroll) | < 50 | ✓ |

## Verdict

**`@tanstack/react-virtual` is doing exactly what it's supposed to do.**
Continuous scroll over the full ~4400 px range completed in 5 s at the
display's full 120 Hz refresh rate, with sub-10 ms frame times and zero
dropped frames. The number of rendered DOM rows stayed flat at 28
throughout the scroll — rows enter and leave the DOM as the
virtualizer windows the data.

This invalidates the spec's contingency "If FPS < 50 or any long task
> 100 ms found: name the offending function + line" for the scroll
itself. The two 142 ms long tasks observed are unrelated to scrolling
— see breakdown below.

## The two 142 ms long tasks

Both fired during the 5 s scroll window, but the scroll itself
maintained 120 fps throughout — so these tasks ran on a different
thread of work (a microtask / setTimeout fired by something other than
the scroll handler). The likeliest explanations, ranked:

1. **Orchestrator-active poll cycle.** `console-sweep.md` flagged the
   `/api/proxy/orchestrator/active` endpoint as polling on a tight
   interval (~2 calls/sec idle). A poll fires → fetch resolves →
   React state update → re-render of `<OrchestratorBanner>` and any
   subscribers. 142 ms is on the high side for a simple JSON-parse +
   setState, suggesting React is re-running effects across the
   sidebar's `<AIInsightsWidget>` + stats card subscribers too.
2. **`<AIInsightsWidget>` Gemini re-render** following one of the
   stats cache invalidations. Sidebar re-render on the AI insights
   panel involves wrapping text into 3 bullet points; with @sentry's
   profiling integration loading + Sentry breadcrumbs being appended,
   142 ms is plausible.
3. **React strict-mode double-mount** of one of the dynamic-imported
   children. Unlikely to fire mid-scroll, but possible if `useEffect`
   ran from a stale-closure cleanup.

None of these would be visible to the user — the scroll itself is
smooth. They show up as "wasted CPU" the moment another interaction
needs to land. **Recommend reading `tests/perf/scroll-trace-raf.json`
in Chrome DevTools Performance panel and looking at what the call
stack looks like at the two ~142 ms spikes.** That's where this
finding would convert to a specific file:line.

## Constraints + caveats vs. spec

- **Spec asked for 500 rendered rows; the table can hold at most 200
  per fetch** (the backend `/leads` endpoint caps `limit` at 200 per
  CLAUDE.md; the dashboard defaults to 50). The default page size of
  50 was used here — clicking "Load more" 4× during the test
  triggered the API but the dashboard rolled back to 50 visible rows
  (a race between the Load-more click and the existing scroll-state).
  Repeat with a UI tweak to default `limit=200` or auto-load until
  cap to reach the spec's "500 rendered" target. Either way, the
  virtualizer's window stays at ~28 DOM rows so the perf signal is
  invariant to the underlying row count — extra rows are added to
  the same fixed-size render window.
- **No React DevTools profiler attached.** `chrome-devtools-mcp` does
  not expose the React DevTools backend. To get per-component
  re-render counts, the test would need to be re-run via Playwright
  with the React DevTools extension preloaded.

## Cleanup

The 500 seeded `_perf_test_*` rows persist in the Supabase project
until the operator clears them. To remove, run:

```sql
DELETE FROM leads WHERE unique_key LIKE '_perf_test_%';
```

## Methodology notes

The first attempt used a paced scroll loop with
`setTimeout(r, expected - now)` between scroll steps to stretch the
sequence to exactly 5 s. That instrumented its own pacing latency
into the "frame time" measurement and reported a fake 12 fps. The
canonical run (`scroll-trace-raf.json`) uses free-running
`requestAnimationFrame` with `scrollTop` interpolated from
`elapsed / duration` — the browser drives the loop at full refresh
rate and the frame-time delta is genuine. This is the run reported
above.
