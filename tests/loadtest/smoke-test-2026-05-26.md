# Live Smoke Test Attempt — 2026-05-26

**Verdict: BLOCKED at step 1. Backend not reachable; smoke cannot proceed.**

## Goal
End-to-end: dispatch → email → unsubscribe → email_suppression row.

## Step 1: Backend `/health` — FAIL

URL: `https://lead-scraper-backend.onrender.com/health` (matches `render.yaml`).

| Attempt | Timeout | Result |
|---|---|---|
| 1 | 30s | curl: (28) 0 bytes received |
| 2 | 60s | curl: (28) 0 bytes received |
| 3 | 75s (after 270s sleep) | 0 bytes; 3x retried `/`, `/health`, HEAD |
| 4 | 8s | 0 bytes |

TLS handshake completes — cert `CN=onrender.com`, SAN matches via wildcard, HTTP/2 stream opens, GET sent. **Origin never writes a byte.** This is the Render edge holding the connection open while the origin app fails to respond.

Cross-checked alternative hostnames:
- `leaddatascraper-backend.onrender.com` → 404 in 0.5s (does not exist)
- `lead-data-scraper-backend.onrender.com` → 404 in 0.5s (does not exist)

## Adjacent signals (smoke prereqs not satisfied)

1. **Vercel frontend**: `lead-data-scraper.vercel.app` → `404 DEPLOYMENT_NOT_FOUND`.
2. **Custom domain `leaddatascraper.com`** → HTTP 200 with `x-powered-by: PHP/8.3.30` and `retry-after: 86400`. A-record `154.12.118.230` — **not** a Vercel (76.76.x) or Render edge (216.24.x) IP. DNS not pointed at this stack.
3. **`api.leaddatascraper.com`** → empty DNS response.
4. **Local shell env**: `RENDER_API_KEY`, `ADMIN_TOKEN`, `INSTANTLY_API_KEY` — none present despite user prereq stating they were.

## Root cause (refined after deeper probe)

**Backend is alive on Render's internal network; only the public ingress is broken.**

Evidence: `POST /api/proxy/metrics` to the live Render-hosted frontend returns `HTTP 401 {"error":"unauthorized"}` in 718 ms. That payload is FastAPI JSON, not a Render edge HTML error. The frontend's `/api/proxy/[...path]` reads `BACKEND_URL` from `render.yaml::fromService.name.property=host`, which gives the internal hostname. So:
- Frontend `lead-scraper-frontend.onrender.com` → 307 redirect with full CSP nonce headers. ✅ live.
- Backend internal hostname (`lead-scraper-backend:<port>` private) → reachable from frontend pod. ✅ live.
- Backend public hostname `lead-scraper-backend.onrender.com` → TLS opens, 0 bytes. ❌ public ingress disabled/misconfigured.

Independent issues:
- `leaddatascraper.com` apex DNS → `154.12.118.230` (PHP host) — DNS not pointed at this stack (separate to fix).
- `lead-data-scraper.vercel.app` → DEPLOYMENT_NOT_FOUND — frontend is hosted on Render not Vercel; the Vercel deploy is gone (or never existed under new account).

## What user must verify before retry

1. **Render dashboard** — for `lead-scraper-backend`: is service status `Live` (green)? Latest deploy log tail: `Application startup complete` line present?
2. **Render dashboard** — does `lead-scraper-frontend` service exist at all under the new account?
3. **Vercel dashboard** — production URL + deploy state.
4. **DNS** — what should `leaddatascraper.com` + `api.leaddatascraper.com` resolve to? Currently the apex points to `154.12.118.230` (PHP host).
5. **Shell** — `echo $ADMIN_TOKEN $INSTANTLY_API_KEY $RENDER_API_KEY` — confirm whether session was supposed to inherit these.

## What we did NOT execute (steps 2–10)

Test row insert, `/admin/dispatch-tick-now`, Instantly send verification, inbox check, unsubscribe click, email_suppression SELECT, idempotency replay, second dispatch tick, Sentry log scan. All gated on step 1.

## Time accounting

- Total wall time on step 1: ~7 minutes (270s sleep + 4 probe rounds).
- Total Render origin time spent waiting on response: ~3.5 min cumulative, 0 bytes received.

## Recovery plan

When backend confirmed live:
1. Re-run step 1 via this file's commands.
2. User inserts test row via Supabase Studio + pastes `id` + `tracking_id`.
3. Resume steps 3–10 in order.
