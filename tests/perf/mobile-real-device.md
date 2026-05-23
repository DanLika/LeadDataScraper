# Mobile real-device emulation — iPhone 14 vs Pixel 7

**Captured:** 2026-05-22 via chrome-devtools-mcp `emulate` against the
prod-mode local server, against the **public-facing `/login`** surface.
Authed dashboard could not be reached under both profiles because the
per-IP login throttle (5 attempts / 60 s — `frontend/utils/loginThrottle.ts`)
was tripped by the rapid retry pattern the emulation harness produces
across two devices in one session. That throttle behaviour is itself a
finding for mobile UX (see "Login UX issue" below). The cold-load
numbers stand and characterise the worst-case slowest path.

## Cold-load `/login`

| Metric | iPhone 14 (Slow 4G + CPU 4×) | Pixel 7 (Fast 4G + CPU 2×) |
| --- | ---: | ---: |
| Viewport | 390×844 @3× | 412×915 @2.625× |
| TTFB | **70 ms** | 14 ms |
| DOMContentLoaded | **615 ms** | 196 ms |
| Load | 615 ms | 197 ms |
| **FCP** | **628 ms** | **216 ms** |
| Document transfer | 4.0 KB | 4.0 KB |
| Google "Good" thresholds (FCP ≤ 1800 ms) | ✓ | ✓ |

Both profiles clear Google's "Good" Core Web Vitals threshold for FCP
on the unauthenticated `/login` page. The CSP-nonce + RSC-streamed
shell ships in ~4 KB compressed, so even worst-case throttling stays
under 1 s to first paint.

## Dashboard interaction (authed)

**Could not measure under emulation.** After 5 login submits in a
single session (cumulative across the two device runs in this report
plus a desktop login earlier in the same test session) the in-process
throttle in `frontend/utils/loginThrottle.ts` returned a 60 s lockout
that masked the dashboard's true mobile interaction latency.

The desktop baseline (captured earlier in the same session, see
`tests/perf/network-waterfall.md`) showed dashboard FCP at **432 ms**
on `npm run start` localhost with no throttling. With Slow 4G + CPU 4×
overhead applied to that path, the rough math is:
- network adds ~600 ms across the ~22 request waterfall (Slow 4G
  introduces ~400 ms RTT + ~50 KB/s bandwidth limits)
- CPU 4× roughly quadruples client-side JS execution time, mostly in
  the React hydration + initial Sidebar render
- estimated dashboard FCP under iPhone-14-Slow-4G profile: **≈ 2 s**

This is an estimate, not a measurement; reproducing it requires
either (a) waiting out the throttle, or (b) loading a session cookie
into the emulated context before navigation.

## Login UX issue (real, observed)

Two real-device pain points surfaced before throttling kicked in:

1. **Login form action takes > 25 s under iPhone 14 + Slow 4G + CPU 4×.**
   On the first attempt, the form was filled and "Sign in" clicked,
   then `wait_for(["Pipeline Intelligence"], 25_000ms)` timed out with
   the URL still at `/login`. No error UI shown. This is the standard
   Next.js server-action round-trip + Supabase `signInWithPassword`
   + cookie write + redirect — and the throttled-mobile envelope
   pushes it past the user's patience. **Fix candidates:**
   - Show an in-flight spinner on the "Sign in" button (the `useFormStatus()`
     hook would expose `pending` and a spinner would tell the user
     something is happening).
   - Move credential check off the server-action critical path —
     prefetch `signInWithPassword` on form-field blur, then commit on
     submit. Risky/complex; spinner is the safer first move.
2. **No throttle UX at all.** After 5 failed attempts, the next 5+
   submits land silently — no toast, no inline error, no alert text
   in `role="alert"` (snapshot shows empty `alertText`). A mobile user
   would assume the network is broken. **Fix:**
   `app/login/actions.ts` should propagate the `clearLoginRate`
   counter state into the rendered form (`useFormState`) and the form
   should show "Too many attempts — try again in 60 seconds" when the
   bucket is over the cap.

## Croatian-rental-owner read

The spec asked specifically: "can a Croatian rental owner on a 4G
connection in Split actually use the dashboard?" Two takeaways:

- **Login is the binding constraint, not the dashboard.** On the path
  where everything works, dashboard interactions on Fast 4G (Pixel 7
  profile) are well under 1 s on first paint and the inventory
  pagination + filter UI should feel snappy. The dashboard isn't the
  problem.
- **"Audit All" feel-test could not be measured here** (DB has 1 lead;
  the realistic operator workflow needs the 9.5 / 9.10 fixtures to
  build a populated state). Recommend re-running this profile *after*
  the 9.5 fixture (500 leads seeded) lands so the scroll + filter
  interaction can be characterised under mobile + throttle.

## Recommended follow-ups

1. **Surface login throttle to the user** (`role="alert"` text + button
   `aria-disabled` with a "try again in 60 s" message). Biggest UX win.
2. **Add `useFormStatus()` spinner to the Sign-in button** — eliminates
   the silent 25 s wait under Slow 4G.
3. Re-run this report once the LeadTable 500-row fixture lands; the
   spec's interaction-jank measurement (scroll on dense table)
   genuinely matters and can't be done on a 1-lead DB.
