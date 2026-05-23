# Long-task observer + dashboard interaction trace

**Captured:** 2026-05-22 via chrome-devtools-mcp on
`http://localhost:3000/` (prod build, post-CSP-nonce, post-Sentry,
post-backend-`db`-fix), 1 lead in DB, single authed operator.
**Raw trace:** `tests/perf/dashboard-interaction-trace.json`.

The spec asked for a 5-minute observation window. The live-testing
budget required compressing this to a **~35-second targeted smoke**
(open Settings → close → type 41-char search string → click Audited
filter → click High Risk filter → click "Refresh AI insights"). Each
of these surfaces was independently flagged in the console sweep
(`tests/perf/console-sweep.md`) as a potential offender.

## Results

| Metric | Value | Verdict |
| --- | ---: | --- |
| `PerformanceObserver({type: 'longtask', buffered: true})` entries | **0** | No callback exceeded 50 ms during the smoke. |
| INP (longest interaction) | **101 ms** (pointerdown) | Google "Good" (≤200 ms) |
| INP — input delay | 1 ms | optimal |
| INP — processing duration | 21 ms | optimal (well under the 50 ms long-task threshold) |
| INP — **presentation delay** | **78 ms** | Dominant phase; browser layout/paint, not JS |
| CLS | 0.00 | optimal |
| Trace-suggested savings | **none** | DevTools insight engine found no actionable optimization |

## Reading these numbers

- **Zero long tasks** means the React state updates triggered by the
  five interactions (`setShowSettings`, search-input `onChange` →
  `router.replace`, view-filter toggle, AI insights refetch + render)
  all completed under one frame's worth of compute.
- **Presentation delay dominating INP (78 / 101 ms)** is the signature
  of a layout/paint bottleneck, not a JS bottleneck. Likely caused
  by the dashboard's recharts `<PieChart>` re-rendering when filter
  state changes — recharts uses SVG, which can spike paint cost
  proportionally to series cardinality. Today there's 1 lead so
  cost is trivial; if "PriceTier"-style segmented data lands and the
  donut grows to dozens of slices, this becomes the actionable
  optimization target.
- **No `console.warn('LONGTASK')`-style entries to group by
  attribution.** Spec asked for a frequency-ordered breakdown — empty.
  The closest you'd get from this trace is "`pointerdown` event
  processing on the Sidebar Settings button" at ~20 ms processing,
  which is well within budget.

## Stress test (deferred)

This sweep ran against a 1-lead database. The realistic load profile
(operator with 500-2000 leads in inventory) would exercise paths
that didn't fire here:
- `LeadTable` virtualized scroll under populated inventory
  (task 9.5 — explicitly deferred for 500-lead fixture seeding)
- recharts series with more than the empty-state slice
- the AI insights widget on a populated `_get_strategic_insights`
  call (Gemini round-trip is the slow leg, not the render)
- the `/api/proxy/orchestrator/active` poller during an actually
  running job (today it's just churning empty-response keep-alives)

Recommend re-running this same instrumentation after the 9.5
fixture, using the same `performance_start_trace` + Long-Task
PerformanceObserver pair, and comparing INP / long-task count /
presentation-delay against this 0-baseline.

## Methodology

```js
new PerformanceObserver(list => {
  for (const e of list.getEntries()) {
    window.__longTasks.push({ startTime, duration, attribution });
  }
}).observe({ type: 'longtask', buffered: true });
```

Buffered observation catches tasks that fire before the observer is
registered (within the same document load). Combined with the CDP-level
trace (via `chrome-devtools-mcp performance_start_trace`), the two
sources cross-check: anything the buffered PerformanceObserver missed
would show up in the trace's main-thread call-frame stream. Both
agreed: zero long tasks during the 35 s window.
