# Live Perf-Test Snapshots

## Live perf-test report inventory (2026-05-22, `fix/csp-nonce-rsc-hydration`)
Live chrome-devtools-mcp sweep against `npm run start` prod build, authed
as `test-lds4@example.com`. Each report is a 2026-05-22 point-in-time
snapshot — re-run before claiming the characteristic still holds.
- `tests/perf/network-waterfall.md` (9.3) — 23 cold requests, 211 KB
  transfer, FCP 432 ms, 0 third-party, 15/22 disk-cache hits on warm.
  **Bugs flagged:** `favicon.ico` revalidates every load (26 KB tax);
  4 `/api/proxy/*` calls fire before any user interaction.
- `tests/perf/console-sweep.md` (9.4) — **P1**: AI insights refresh
  passes a non-`AbortSignal` to `fetch({signal})`. **P2**:
  orchestrator-active poller ~2 calls/sec idle (no
  visibility-pause/backoff). **P2**: search input no debounce (RSC
  fetch per keystroke). **P3 a11y**: form field without id/name on
  `/` + `/campaigns`. Sentry `disableLogger` deprecation warning.
- `tests/perf/scroll-analysis.md` (9.5) + `scroll-trace-raf.json` —
  **119.9 FPS, max 9.4 ms frame, 0 dropped frames** across 600-frame
  5 s continuous scroll. `@tanstack/react-virtual` keeps DOM at ~28
  row nodes throughout. CLS 0.00.
- `docs/font-audit.md` (9.7) — confirmed silent fallback: `Inter`
  declared but zero `.woff*` ship. Body → `system-ui`, form controls
  → UA `Arial`. Pick: drop the declaration OR wire `next/font/google`.
- `tests/perf/mobile-real-device.md` (9.9) — iPhone 14 + Slow 4G +
  CPU 4×: `/login` FCP 628 ms (Good). Pixel 7 + Fast 4G + CPU 2×:
  FCP 216 ms. **Login UX bug**: no Sign-in spinner; no toast on
  throttle (`frontend/utils/loginThrottle.ts` 5/60 s).
- `tests/perf/long-tasks.md` (9.11) + `dashboard-interaction-trace.json`
  — INP 101 ms (Good, edge; 78 ms presentation delay dominant), CLS
  0.00, 0 long tasks during a 35 s 5-interaction smoke.

Skipped / deferred:
- **9.6 Coverage** — `chrome-devtools-mcp` doesn't expose CDP
  `Coverage`. Re-run via Playwright if needed.
- **9.8 Live CSP/HSTS** — spec required "live deployed URL"; not
  available in the agent session.
- **9.10 Full pipeline live** — real Gemini + Maps scrape ($,
  operator DB writes). Spec says quarterly cadence; operator-triggered.
- **9.12 Visual smoke** — `frontend/e2e/__screenshots__/` does not
  exist; spec files present without baselines.

