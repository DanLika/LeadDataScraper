# Console + page error sweep — 4 routes (live session)

**Captured:** 2026-05-22 via chrome-devtools-mcp against `localhost:3000`
on the `fix/csp-nonce-rsc-hydration` branch (post-CSP-nonce fix,
post-Sentry install, post-backend `db`/`router` priming fix).
**Auth:** `test-lds4@example.com` (single-operator).

Smoke action set per route: navigate cold, then exercise visible
non-destructive controls (modals open/close, sidebar filters, search
typing, click "Refresh AI insights", form fills, dropdown opens,
sign-out).

## Findings table

| Sev | Route | Trigger | Surface | Detail |
| --- | --- | --- | --- | --- |
| **P1** | `/` | Click "Refresh AI insights" (sidebar) | `console.error` | `Insights fetch failed: TypeError: Failed to execute 'fetch' on 'Window': Failed to read the 'signal' property from 'RequestInit': Failed to convert value to 'AbortSignal'.` — the refresh handler is passing a non-`AbortSignal` value (likely the `AbortController` itself, or an unwrapped `null`/`undefined`) to `fetch({ signal })`. Real bug. |
| **P2** | `/` | Type in "Search leads" textbox | network | **No debounce.** Every keystroke fires a new RSC navigation (`GET /?q=U`, `?q=Un`, `?q=Unk`, …, `?q=Unknown` — 7 fetches for one 7-char word). Each is a round-trip including RSC payload. Spec'd behaviour or accidental? Either way, on a slow link this multiplies tail-latency by N. |
| **P2** | `/` | Sit idle on dashboard | network | `GET /api/proxy/orchestrator/active` fires **on a tight interval** (14+ calls in ~30 s observed) plus `GET /api/proxy/leads?limit=50` re-polls every ~5 s. Suspect a `setInterval` without a backoff and without a cleanup on tab-hidden / pause-when-no-active-job. |
| **P3 (a11y)** | `/`, `/campaigns` | Form mount | `[issue]` | `A form field element should have an id or name attribute` — 1 instance per page. DevTools Issues panel flags it as `FormInputWithNoLabelIssue`. Likely the search textbox or a hidden `<input>` that has `aria-label` only. |
| **P0** (pre-fix) | `/`, `/insights`, `/campaigns` | First paint | `console.error` ×6, fetch 500 | Three backend endpoints (`/leads`, `/insights`, `/orchestrator/active`) returned 500 with `NameError: name 'db' is not defined`. **Root cause + fix landed in this session** (`backend/main.py` lifespan now primes lazy globals via `sys.modules[__name__]` — PEP 562 doesn't fire `__getattr__` for bare names inside functions). Listed here for completeness; post-fix sweep is clean. |

## Per-route summary (post all fixes)

| Route | `console.error` | `console.warn` | Uncaught exc | Unhandled promise rej | Failed network |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/` | 1 (AbortSignal P1) | 0 | 0 | 0 | 0 (after backend fix) |
| `/insights` | 0 | 0 | 0 | 0 | 0 |
| `/campaigns` | 0 | 0 | 0 | 0 | 0 |
| `/login` (post-signout) | 0 | 0 | 0 | 0 | 0 |

A11y `[issue]`-level messages (form-field id/name) are flagged on `/`
and `/campaigns`. These show in the DevTools "Issues" tab but do not
register as `console.error` / `console.warn`.

## Allowed console output

The sweep saw zero non-issue `console.warn` lines. The repo's
allowlist (the things you'd *expect* to see and not panic) is therefore
empty — any new `console.warn` introduced by future code should be
investigated before being allowlisted.

## Deprecation warnings

None observed at the browser level. Server-side, the frontend
log carries:

```
[@sentry/nextjs] DEPRECATION WARNING: disableLogger is deprecated and
will be removed in a future version. Use webpack.treeshake.removeDebugLogging
instead.
```

This is a Sentry-config message from `next.config.ts`; fix is a
one-line config update.

## Recommended follow-ups (smallest → largest)

1. **A11y form-field warning** — find the `<input>` without `id`/`name`,
   add one. Look at `Sidebar.tsx` and `LeadTable.tsx` first (those own
   most of the inputs visible on `/`).
2. **AbortSignal P1** — in the `Sidebar.tsx` insights-widget refresh
   handler, ensure the value passed as `signal` is
   `abortController.signal`, not the controller. Reproduces on every
   "Refresh AI insights" click.
3. **Sentry `disableLogger` deprecation** — in `next.config.ts`'s
   `withSentryConfig({ … })` options, swap `disableLogger: true` →
   `webpack: { treeshake: { removeDebugLogging: true } }`.
4. **Search debouncing** — wrap the search input in a 200–300 ms debounce
   before calling `router.replace()`. Per-keystroke RSC navigation is
   expensive on tail latency and pollutes server logs.
5. **Orchestrator polling backoff** — replace the fixed-interval
   `setInterval` with: poll while a job is running, exponentially back
   off when none is active, and pause on `document.visibilityState !==
   'visible'`. Today the dashboard idle-polls at ~2 calls/sec.
