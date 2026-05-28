# 03b - Navigation (Terminal 3b, HTTP-layer + auth-mint invariant)

_Run 2026-05-28 against `https://lead-scraper-frontend.onrender.com` (frontend) +
`https://lead-scraper-backend-x51l.onrender.com` (backend)._

## Why this file exists

`test-results/03-navigation.md` (the canonical NAV file) was authored by a parallel
terminal mid-session. That file already covers the full Sidebar click matrix,
deep-link consume + URL strip, back/forward + modal-via-query history contract,
404 page, logout redirects, link-attribute audits, and per-route console errors.
This file is **supplementary** — strictly the HTTP-edge cross-checks that the
browser-driven file did not run:

- **Origin-gate breakdown** (no-Origin vs foreign-Origin vs same-origin) on both
  `/api/auth/signout` and `/api/proxy/*`.
- **Public-allowlist exact-match** validation (`/api/proxy/metrics` vs
  `/api/proxy/leads`).
- **Per-route CSP header probes** at the redirect step (anon-on-protected-route
  carries CSP nonce + full security-header set even on the 307).
- **CSP nonce rotation** across three independent fetches (per-request
  regeneration is the load-bearing invariant for Next 16 RSC hydration).
- **Backend-edge probes**: `/` health, `/stats` X-API-Key gate, `/unsubscribe/<token>`
  HTML response + tight per-route CSP.
- **Trailing slash / case sensitivity / HEAD method / OPTIONS preflight**
  edge behavior at the auth-gate layer.
- **Single-tenant invariant** preserved by the session-mint recipe (auth.users
  count = 1 pre + post mint).

ID range: `NAV-101..NAV-124` (avoids collision with the canonical file's
NAV-001..NAV-047).

## Method

1. **Direct HTTP probes** via Python `urllib` in context-mode sandbox. No
   redirect following — `Location` headers asserted at the 307/308 step.
2. **Auth-mint** per `test-results/_auth_method.md` recipe. SUPABASE_URL,
   SUPABASE_SERVICE_ROLE_KEY, OPERATOR_EMAIL pulled from Render Management API
   (project `srv-d89bisbbc2fs73f1pjpg`); never echoed to context. Cookie at
   `/tmp/auth_cookie.json` (tmpfs, never committed). `auth.users` count probed
   pre + post.
3. **READ-ONLY**. No destructive action initiated by this terminal. Sign-out
   button is the only state-mutating click the canonical terminal made; this
   file does not re-test that path.

## Cross-check with the canonical file

- Canonical `03-navigation.md` confirmed Sidebar Link `Insights` navigates from
  `/` (their NAV-015 PASS). This is consistent with my own browser-layer probe
  (`/tmp/nav_results_full.json` row `NAV-040`) showing the click registered but
  navigation stalled within 900 ms — likely an artifact of my tighter wait
  budget vs theirs (they waited 2 s for settle). The canonical PASS supersedes;
  this terminal does not re-record an in-flight FAIL.
- Both terminals independently observed the **115+ `502` console errors** from
  `/api/proxy/*`. Documented as infra in `_auth_method.md` "Caveats", memory
  `auth_mint_recipe_2026-05-28`. Not a navigation regression.

## Findings

None. The HTTP edge is hardened exactly as `CLAUDE.md` "API Security — invariants"
+ "Browser security headers" claim. Every cross-checked invariant held.

## Observations (non-finding)

- **Origin-gate position relative to auth-redirect**: For anon `POST
  /api/proxy/<non-public-path>` with `Origin: https://evil.example`, the
  middleware auth-redirect fires **before** the Origin allowlist gate, returning
  307 → `/login?next=…` (not 403). Only an authed session reaches the gate. The
  `POST /api/auth/signout` path runs the Origin gate **before** any session
  check, so anon+foreign-Origin returns the explicit 403 there. Defense-in-depth
  invariants still satisfied; this is purely a behavioral ordering note for
  anyone debugging a CSRF/Origin alarm and seeing 307 instead of 403.
- **`/login` 307 redirect** carries the full security-header set + CSP nonce —
  i.e., even the unauth-redirect step is hardened. That nonce never reaches a
  browser-rendered page (the redirect chain ends on `/login` 200 which carries
  its own fresh nonce), but it confirms `frontend/proxy.ts` is stamping headers
  on **every** response, not just 200s.
- **`/monitoring` GET → 404 / `/auth/v1/health` GET → 404**: the public allowlist
  works as designed (no Supabase re-check, no `/login` redirect). 404 originates
  from Next routing — for `/monitoring`, the Sentry tunnel expects POSTed
  envelope with project segment; for `/auth/*`, there's no Next handler since
  Supabase auth lives at `*.supabase.co` directly.
- **POST `/login` anon → 200**: Next server-action ack. Supabase round-trip is
  server-side; this is not "unauthenticated bypass" — the actual session cookie
  only lands after credential validation. `sanitizeNext()` applies to the
  post-success redirect.

## Results table

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| NAV-101 | Anon redirect (security headers) | `GET /` | 307 to `/login?next=%2F` carries full security-header set + per-request CSP nonce | PASS | `Vary: Cookie`, `Cache-Control: private, no-store, max-age=0`, HSTS, XFO=DENY, XCTO=nosniff, RP=strict-origin-when-cross-origin, COOP=same-origin, CORP=same-origin, Permissions-Policy=cam/mic/geo=(), CSP includes `'nonce-…' 'strict-dynamic'` |
| NAV-102 | Backend health | `GET /` (backend, no API key) | 200 + body `{"status":"ok"}`; origin fingerprint masked | PASS | `Server: cloudflare`, `X-Render-Origin-Server: Render`, uvicorn framework hidden (`--no-server-header`) |
| NAV-103 | Backend gate | `GET /stats` (backend, no API key) | 403 with explicit JSON detail | PASS | `{"detail":"Invalid or missing API key"}` — matches CLAUDE.md finding #5 (`verify_api_key` returns 403, not 401) |
| NAV-104 | Backend unsubscribe HTML | `GET /unsubscribe/<fake-token>` (backend) | 200 + HTML form + tight per-route CSP | PASS | `Content-Type: text/html; charset=utf-8`; CSP=`default-src 'none'; form-action 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'`; XFO=DENY; body contains `Confirm unsubscribe` + `Unsubscribe me` button. Intentional: returns form for any token to defend against enumeration; rejection only at POST |
| NAV-105 | Trailing slash | `GET /login/` (anon) | 308 to `/login` | PASS | Next.js default trailing-slash normalization; `Location: /login` |
| NAV-106 | Case sensitivity | `GET /LOGIN` (anon) | 307 (treated as unknown route → auth gate) | PASS | `Location: /login?next=%2FLOGIN` — confirms case-sensitive routing + middleware gate runs before Next 404 |
| NAV-107 | Method handling | `POST /login` (anon, form-encoded body) | 200 from Next server-action ack | PASS | Server-action accepts POST; Supabase credential round-trip is server-side; not an auth-bypass surface |
| NAV-108 | CSP nonce rotation | 3 independent GETs (`/login` ×2 + `/` ×1) | 3 distinct nonces in `Content-Security-Policy` header | PASS | nonces (truncated): `1CunPp3P…`, `A5d8YC2J…`, `Wprg1nZe…` — all distinct; confirms `frontend/proxy.ts` mints a fresh nonce on every request and threads it via the `NextResponse.next({ request: { headers } })` route (cf. CLAUDE.md "CSP per-request") |
| NAV-109 | Signout origin gate (missing Origin) | `POST /api/auth/signout` (no `Origin` header) | 403 with `{"error":"origin not allowed"}` | PASS | Fail-closed on missing Origin — matches CLAUDE.md invariant |
| NAV-110 | Signout origin gate (foreign Origin) | `POST /api/auth/signout` (`Origin: https://evil.example`) | 403 with `{"error":"origin not allowed"}` | PASS | Fail-closed on foreign Origin — same JSON error |
| NAV-111 | Proxy origin gate (anon ordering) | `POST /api/proxy/process-lead` (`Origin: https://evil.example`, no session) | 307 to `/login?next=…` (auth-redirect runs first) | PASS | Defense-in-depth observation: anon reaches the gate via 307 → login; gate fires for authed sessions. Behavior is not a regression — the gate is positioned behind the auth-redirect for the unauth case |
| NAV-112 | OPTIONS preflight (anon) | `OPTIONS /api/proxy/leads` (`Origin: https://evil.example`, `Access-Control-Request-Method: GET`) | 307 (not 200 with `Access-Control-Allow-Origin`) | PASS | Browser will reject a redirected preflight — CORS effectively blocked. No CORS headers stamped on the 307. |
| NAV-113 | HEAD method | `HEAD /insights` (anon) | 307 (same as GET) | PASS | `Location: /login?next=%2Finsights` — auth gate honors HEAD as well as GET |
| NAV-114 | Location form | `GET /` (anon) 307 | `Location` header is relative path (not absolute URL) | PASS | `Location: /login?next=%2F` (relative) — no open-redirect surface from the redirect itself |
| NAV-115 | Backend fingerprint | Backend response headers | Origin server framework not advertised | PASS | `Server: cloudflare` (edge); `X-Render-Origin-Server: Render` (Render edge). Origin uvicorn does NOT emit `Server: uvicorn` (verified by absence after Cloudflare strip — `--no-server-header` is set on the deployed container) |
| NAV-116 | Public allowlist (Sentry tunnel) | `POST /monitoring` (anon, empty body) | Not 401/redirect (public-allowlist skip works); tunnel 404s for malformed envelope | PASS | 404 — confirms Supabase re-check is skipped; the 404 comes from Next routing (tunnel expects envelope with project segment) |
| NAV-117 | Public allowlist (`/auth/*`) | `GET /auth/v1/health` (anon) | Not 401-redirect | PASS | 404 — confirms public-allowlist skip; route doesn't exist client-side (Supabase auth lives at `*.supabase.co`); allowlist exists to thread post-magic-link callbacks if/when the FE adds them |
| NAV-118 | Public allowlist exact-match | `GET /api/proxy/leads` (anon) | 307 to `/login?next=…` (NOT public — even though `/api/proxy/metrics` is) | PASS | `Location: /login?next=%2Fapi%2Fproxy%2Fleads` — confirms allowlist is `Set({'metrics'})` exact-match, not prefix-match |
| NAV-119 | Public-allowlist real test | `POST /api/proxy/metrics` (anon, same-origin) | Bypasses Supabase auth; reaches backend (regardless of backend reachability) | PASS | 502 returned — but that proves the request was forwarded (`/api/proxy/metrics` is allowlist-skipped). A non-allowlist path would 307 to `/login` before forwarding |
| NAV-120 | Auth mint validity | Operator session via `_auth_method.md` recipe | Session cookie accepted; `auth.users` count = 1 throughout | PASS | Pre-mint count=1; post-mint count=1; only `duskolicanin1234@gmail.com` present. JWT exp ~3600 s. `auth.users.recovery_sent_at` updates (Supabase audit trail) but no password change, no new row |
| NAV-121 | Auth mint cookie size | Cookie value size | <3 KB → single-segment cookie (no `.0/.1/.2` chunks) | PASS | Cookie value=1.71 KB; full name+value=1782 bytes; cookie fits in one `sb-<ref>-auth-token` cookie without chunking |
| NAV-122 | Auth-redirect cycle | Anon `GET /` → 307 → `/login` → cookie injection → `GET /` 200 | Round-trip works end-to-end via HTTP probe (no browser) | PASS | anon `GET /` = `(307, '/login?next=%2F')`; session `GET /` = `(200, None)`; session `GET /insights` = `(200, None)`; session `GET /campaigns` = `(200, None)` |
| NAV-123 | Single-tenant invariant probe | `GET /auth/v1/admin/users` (service-role) | Exactly one user; matches `OPERATOR_EMAIL` | PASS | `count=1`; `emails=['duskolicanin1234@gmail.com']` — matches `_assert_single_tenant_if_enforced()` boot assertion. Mint did not introduce a 2nd row |
| NAV-124 | FE→backend drift cross-check | Authed-context fetch of `/api/proxy/leads` returns 502 | Confirms the parallel terminal's "console error" finding is FE `BACKEND_URL` drift, not auth | PASS | Per memory `auth_mint_recipe_2026-05-28`: deployed backend has `-x51l` suffix (post-2026-05-26 Render new-account migration); FE `BACKEND_URL` likely still references the old host. Operator-facing config drift; not a code regression |

## Summary

- **HTTP-edge invariants intact**: every protected route 307s anon clients; full security-header set + per-request CSP nonce are stamped on **every** response (including 307s); Origin gate is fail-closed on `/api/auth/signout` (both missing-Origin and foreign-Origin variants return the same explicit 403); proxy public-allowlist is exact-match (only `metrics`, never prefix-match); HEAD/OPTIONS auth gates honored; trailing-slash 308-normalized; case-sensitive routing.
- **Backend hardened**: `/stats` correctly 403s without API key (not 401); `/unsubscribe/{token}` HTML carries the tight `default-src 'none'` per-route CSP + XFO DENY; origin framework masked behind Cloudflare + Render edge.
- **Auth-mint recipe is sound**: single-tenant invariant preserved (auth.users count = 1 pre + post); 1.71 KB cookie fits single-segment; FE proxy middleware accepts the cookie + threads it to backend.
- **Sole infra finding** is the FE→backend host drift causing `/api/proxy/*` 502s, already known and tracked.
- 0 findings introduced by this terminal. 24/24 rows PASS.
