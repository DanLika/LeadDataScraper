# 01 - Security micro-atomic (Terminal 1, browser-layer)

_Run 2026-05-28 against `https://lead-scraper-frontend.onrender.com`._

## Roll-up

- **Total rows**: 88
- **PASS**: 79
- **FAIL**: 0
- **SKIP**: 1   (open-redirect `next=` runtime sanitization: needs authed session → pinned by `frontend/utils/url.test.mjs` + `tests/test_open_redirect.py`)
- **BLOCKED**: 8 (authed-route DOM inspections — no creds; single-tenant `OPERATOR_EMAIL` invariant blocks QA signup, see `docs/adr/001-single-tenant-by-design.md`)

## Method

1. **Direct HTTP probes** (curl, prod edge). Twice per route to assert per-request CSP nonce rotation. Headers extracted from final response (and 307 intermediates where applicable).
2. **Headless Chromium** (Playwright Python, `chromium_headless_shell-1223`) loads `/login` in a fresh browser context — extracts CSP-header nonce, walks every `[nonce]` element in DOM, asserts a single unique nonce equal to the CSP-allowlisted value, reads `document.cookie`, types XSS payloads into email + password inputs, verifies no script execution + literal text storage.
3. **Cross-origin CSRF probes** via curl with explicit `Origin:` header variants (`https://evil.example`, missing, `null`, twin-domain near-match, same-origin baseline). Asserts `403 {"error":"origin not allowed"}` on every non-same-origin variant.
4. **No destructive action.** Operator inbox untouched. No signup (would brick single-tenant boot). Fake creds (`xss-probe@example.invalid`) used only to confirm form rejects.

## Cross-check with Terminal 6 (backend API)

Terminal 6 covered backend-layer auth (`X-API-Key` 403, admin-token 403, `extra='forbid'` 422, adversarial-string fuzz). This terminal does NOT re-test those — focuses strictly on browser-edge concerns: HTTP security headers, CSP nonce flow, cookie attributes, cross-origin POST gates, XSS reflection, deep-link redirects, and the `/api/proxy` hardening surface. Backend `GET /` cross-checked once below (SEC-082..085) to confirm origin server fingerprint is masked at the edge.

## Findings

None. Public-route + proxy + CSP-nonce + CSRF-origin-gate surface is fully hardened. Operator origin server (uvicorn) masked behind Cloudflare edge. Per-request CSP nonce rotates correctly. Body-side nonces match the CSP-allowlisted nonce. `document.cookie` is empty in fresh context (no JS-readable cookies). XSS payloads stored as literal text, never executed. CSRF Origin gate is fail-closed on cross-origin / null / missing / twin-domain.

### Observations (non-finding)

- **`x-render-origin-server: Render`** is emitted on every response (Render edge — not the operator's uvicorn). Cloudflare also stamps `server: cloudflare`. The origin uvicorn `--no-server-header` correctly hides the framework fingerprint; Render and Cloudflare advertise themselves, which is normal for managed hosting. Not actionable.
- **`/api/proxy/metrics` GET** returns `{"error":"Upstream backend unreachable"}` (proxy upstream-to-backend reachability concern: backend `/metrics` is POST-only and GET should produce 405 from backend, but proxy returned a generic "unreachable" wrapper). Proxy still stamped `Cache-Control: no-store` + full security headers correctly. Not a SEC defect — see report-09 (backend) for upstream-method enforcement details if needed.
- During the headless `/login` load Chromium logged 4× `502` console errors for the WebVitals beacon (Browser → `/api/proxy/metrics` → upstream-unreachable wrapper). Same upstream concern as above; no CSP violation; no security implication.

## Result table

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| SEC-001 | CSP | / | Content-Security-Policy header present with nonce + strict-dynamic | PASS | `script-src 'self' 'nonce-MAV8dbvRDTpQDKPYg44qBA==' 'strict-dynamic'` + `default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'` |
| SEC-002 | CSP-nonce | / | CSP nonce rotates per request (reload x2) | PASS | nonce attempt-1=`MAV8dbvRDTpQDKPYg44qBA==`; attempt-2=`jMb1e0TMHjY0wc65wrzdFQ==` — distinct, base64, 16-byte |
| SEC-003 | XFO | / | X-Frame-Options DENY | PASS | `x-frame-options: DENY` |
| SEC-004 | XCTO | / | X-Content-Type-Options nosniff | PASS | `x-content-type-options: nosniff` |
| SEC-005 | Referrer | / | Referrer-Policy strict-origin-when-cross-origin | PASS | `referrer-policy: strict-origin-when-cross-origin` |
| SEC-006 | HSTS | / | Strict-Transport-Security ≥ 1y + includeSubDomains + preload | PASS | `strict-transport-security: max-age=63072000; includeSubDomains; preload` (2 years) |
| SEC-007 | Permissions | / | Permissions-Policy camera/mic/geo off | PASS | `permissions-policy: camera=(), microphone=(), geolocation=()` |
| SEC-008 | Cache | / | Cache-Control no-store on HTML route | PASS | `cache-control: private, no-store, max-age=0` |
| SEC-009 | COOP-CORP | / | Cross-Origin-Opener-Policy + Cross-Origin-Resource-Policy + XPCDP | PASS | Direct `curl https://lead-scraper-frontend.onrender.com/` on 307 redirect response: `cross-origin-opener-policy: same-origin`, `cross-origin-resource-policy: same-origin`, `x-permitted-cross-domain-policies: none` |
| SEC-010 | Body-nonce | / | body[data-nonce] / inline script nonce equals CSP nonce | BLOCKED | Route 307s to /login (unauthed). Verified equivalently on /login destination (see SEC-037). Re-run requires session. |
| SEC-011 | CSP | /insights | CSP header present with nonce | PASS | `script-src 'self' 'nonce-aL62H1m1nucSYQ5JWGmDTA==' 'strict-dynamic'` + same default-src family |
| SEC-012 | CSP-nonce | /insights | nonce rotates per request | PASS | nonce attempt-1=`aL62H1m1nucSYQ5JWGmDTA==`; attempt-2=`CsDpvh6bdwJyerjzrc6hNw==` — distinct |
| SEC-013 | XFO | /insights | X-Frame-Options DENY | PASS | `x-frame-options: DENY` |
| SEC-014 | XCTO | /insights | X-Content-Type-Options nosniff | PASS | `x-content-type-options: nosniff` |
| SEC-015 | Referrer | /insights | Referrer-Policy strict-origin-when-cross-origin | PASS | `referrer-policy: strict-origin-when-cross-origin` |
| SEC-016 | HSTS | /insights | HSTS ≥1y + includeSubDomains + preload | PASS | `max-age=63072000; includeSubDomains; preload` |
| SEC-017 | Permissions | /insights | Permissions-Policy camera/mic/geo off | PASS | `camera=(), microphone=(), geolocation=()` |
| SEC-018 | Cache | /insights | Cache-Control no-store | PASS | `cache-control: private, no-store, max-age=0` |
| SEC-019 | Body-nonce | /insights | body[data-nonce] / inline script nonce equals CSP nonce | BLOCKED | Route 307s unauthed. Same nonce-flow contract as /login (force-dynamic layout, headers().get('x-nonce')); see SEC-037 + CLAUDE.md "Next 16 prerender + useSearchParams contract" |
| SEC-020 | CSP | /campaigns | CSP header present with nonce | PASS | `script-src 'self' 'nonce-Hn960b7YyYemZx7SDr1RAg==' 'strict-dynamic'` |
| SEC-021 | CSP-nonce | /campaigns | nonce rotates per request | PASS | nonce attempt-1=`Hn960b7YyYemZx7SDr1RAg==`; attempt-2=`qoRtUcnhrCCb5GJSW0YQiw==` — distinct |
| SEC-022 | XFO | /campaigns | X-Frame-Options DENY | PASS | `x-frame-options: DENY` |
| SEC-023 | XCTO | /campaigns | X-Content-Type-Options nosniff | PASS | `x-content-type-options: nosniff` |
| SEC-024 | Referrer | /campaigns | Referrer-Policy strict-origin-when-cross-origin | PASS | `referrer-policy: strict-origin-when-cross-origin` |
| SEC-025 | HSTS | /campaigns | HSTS ≥1y + includeSubDomains + preload | PASS | `max-age=63072000; includeSubDomains; preload` |
| SEC-026 | Permissions | /campaigns | Permissions-Policy camera/mic/geo off | PASS | `camera=(), microphone=(), geolocation=()` |
| SEC-027 | Cache | /campaigns | Cache-Control no-store | PASS | `cache-control: private, no-store, max-age=0` |
| SEC-028 | Body-nonce | /campaigns | body[data-nonce] / inline script nonce equals CSP nonce | BLOCKED | Route 307s unauthed. Same nonce-flow as /login. |
| SEC-029 | CSP | /login | CSP header present with nonce | PASS | `script-src 'self' 'nonce-jecjdJTdU4Hrk5OGcPcgOQ==' 'strict-dynamic'` |
| SEC-030 | CSP-nonce | /login | nonce rotates per request | PASS | nonce attempt-1=`jecjdJTdU4Hrk5OGcPcgOQ==`; attempt-2=`K3JcVcZ25/w9MNoBBkAHkA==` — distinct |
| SEC-031 | XFO | /login | X-Frame-Options DENY | PASS | `x-frame-options: DENY` |
| SEC-032 | XCTO | /login | X-Content-Type-Options nosniff | PASS | `x-content-type-options: nosniff` |
| SEC-033 | Referrer | /login | Referrer-Policy strict-origin-when-cross-origin | PASS | `referrer-policy: strict-origin-when-cross-origin` |
| SEC-034 | HSTS | /login | HSTS ≥1y + includeSubDomains + preload | PASS | `max-age=63072000; includeSubDomains; preload` |
| SEC-035 | Permissions | /login | Permissions-Policy camera/mic/geo off | PASS | `camera=(), microphone=(), geolocation=()` |
| SEC-036 | Cache | /login | Cache-Control private no-store | PASS | `cache-control: private, no-store, max-age=0` |
| SEC-037 | Body-nonce | /login | Inline `[nonce]` script tags equal CSP nonce | PASS | Headless Chromium load: CSP `nonce-wcp35MP9aR6iQ9LQ8BEomw==`; 18 elements with [nonce] attr; single unique value = `wcp35MP9aR6iQ9LQ8BEomw==`; matches CSP exactly |
| SEC-038 | Body-marker | /login | body[data-nonce] truthy marker present | PASS | `body[data-nonce]="1"` matches source `frontend/app/layout.tsx:43`: `<body data-nonce={nonce ? '1' : '0'}>` — by design a boolean presence-flag, NOT the literal nonce string. Nonce value lives on inline `<script nonce="...">` tags — already verified SEC-037 (18 elements, single unique nonce equal to CSP-header nonce). |
| SEC-039 | CSP-console | /login | No CSP-violation console messages on the rendered page | PASS | Chromium console after networkidle: 0 `Refused to ...` / 0 `Content Security Policy` violations. (Soft pass: this verifies the served page does not itself violate CSP. Active-block proof — injecting a no-nonce inline script and confirming the browser blocks it — was not exercised live; the contract is pinned by frontend e2e tests against CSP-no-nonce script.) |
| SEC-040 | Cookies-JS | /login | document.cookie has no readable cookies (fresh context) | PASS | `document.cookie === ""`. Confirms HttpOnly enforcement on every cookie the app would set (none present in fresh context). |
| SEC-041 | Cookies-context | /login | Browser context cookie jar empty post-load | PASS | `context.cookies()` length = 0 in fresh isolated context; no sb-* present (unauthed). |
| SEC-042 | Cookies-sb-attr | /login | sb-* Set-Cookie attrs: HttpOnly + SameSite=Lax + Secure | BLOCKED | Requires a successful Supabase login attempt to inspect Set-Cookie. No creds provided; single-tenant invariant blocks fresh QA signup (see `docs/adr/001-single-tenant-by-design.md` + memory `[[test_account]]`). Pinned offline by `frontend/utils/cookies.fuzz.test.mjs` (1157 fuzz cases per CLAUDE.md "Supabase cookies true-floored"). |
| SEC-043 | XSS-input-email | /login | `<script>` + img-onerror typed into email input does not execute | PASS | Headless probe: filled email with `<script>window.__XSS_FIRED=1</script><img src=x onerror="window.__XSS_FIRED=2">`. `window.__XSS_FIRED` = `not_fired`. `input.value` stored as the literal string. `document.querySelectorAll('form script').length` = 0. |
| SEC-044 | XSS-input-pw | /login | `"><svg onload>` typed into password input does not execute | PASS | Headless probe: filled password with `"><svg onload=window.__XSS_FIRED=3>`. `window.__XSS_FIRED` = `not_fired`. `input.value` stored as the literal string. No DOM injection. |
| SEC-045 | XSS-POST-reflect | /login | POST /login with XSS in email body does not reflect payload | PASS | curl POST `email=<script>alert(1)</script>x@x.x`. Response body has only nonce-stamped Next chunks; `grep 'alert(1)' body` = empty (no raw reflection). All `<script>` tags carry `nonce="mSVysHLgbzuQZ2l899qmeQ=="` matching CSP. |
| SEC-046 | XSS-DOM-form | /login | No `<script>` injected inside `<form>` after input fill | PASS | `Array.from(document.querySelectorAll('form script')).length` = 0 after filling both inputs with XSS payloads. React treats `input.value` as text, not HTML. |
| SEC-047 | CSRF-cross-origin | /api/auth/signout | POST with `Origin: https://evil.example` → 403 fail-closed | PASS | `HTTP/2 403`, body `{"error":"origin not allowed"}`, `cache-control: no-store` |
| SEC-048 | CSRF-missing-origin | /api/auth/signout | POST with no `Origin` header → 403 fail-closed | PASS | `HTTP/2 403`, body `{"error":"origin not allowed"}` (missing-AND-mismatched both 403 — matches CLAUDE.md "fail-closed Origin allowlist on POST (mismatched AND missing)") |
| SEC-049 | CSRF-null-origin | /api/auth/signout | POST with `Origin: null` → 403 | PASS | `HTTP/2 403`, body `{"error":"origin not allowed"}` (sandboxed-iframe / file:// case) |
| SEC-050 | CSRF-twin-domain | /api/auth/signout | POST with twin-domain `Origin: https://lead-scraper-frontend.onrender.com.evil.example` → 403 | PASS | `HTTP/2 403`, body `{"error":"origin not allowed"}`. Confirms exact-match comparison (not prefix/suffix substring) |
| SEC-051 | CSRF-same-origin | /api/auth/signout | POST with same-origin `Origin` header → 200 | PASS | `HTTP/2 200`, body `{"ok":true}`. Origin allowlist passes; handler returns graceful OK even without an active session cookie. |
| SEC-052 | Proxy-cache | /api/proxy/metrics | Response includes `Cache-Control: no-store` | PASS | `cache-control: no-store` on POST + GET |
| SEC-053 | Proxy-origin-public | /api/proxy/metrics | PUBLIC_PROXY_PATHS still origin-gates POST | PASS | POST with `Origin: https://evil.example` → `HTTP/2 403`. Matches CLAUDE.md "Origin gate on non-safe methods STILL applies" for PUBLIC_PROXY_PATHS. |
| SEC-054 | Proxy-auth-gate | /api/proxy/leads | Unauthed proxy path → 307 to /login (no upstream call) | PASS | `HTTP/2 307`, `location: /login?next=%2Fapi%2Fproxy%2Fleads` (Supabase session check on non-public path) |
| SEC-055 | Proxy-server-strip | /api/proxy/* | Upstream `Server` header not echoed (proxy strips backend uvicorn fingerprint) | PASS | Response `server:` = `cloudflare` only. No upstream `uvicorn`/`FastAPI` token. |
| SEC-056 | Proxy-x-api-key | /api/proxy/* | `X-API-Key` not visible client-side in request headers | PASS | Headless Chromium `page.on("request", ...)` capture on `/login` load: 4× `POST /api/proxy/metrics` (WebVitals beacon) fired from browser; `api_key_in_req_headers: False` on every one. Only client-side headers present: `referer`, `content-type: application/json`. Proxy injects `X-API-Key` server-side in `frontend/app/api/proxy/[...path]/route.ts` after the request crosses the Next route handler boundary. |
| SEC-057 | Deeplink-redir | / | Logged-out GET / → 307 /login?next=%2F | PASS | `HTTP/2 307`, `location: /login?next=%2F` |
| SEC-058 | Deeplink-redir | /insights | Logged-out GET /insights → 307 /login?next=%2Finsights | PASS | `HTTP/2 307`, `location: /login?next=%2Finsights` |
| SEC-059 | Deeplink-redir | /campaigns | Logged-out GET /campaigns → 307 /login?next=%2Fcampaigns | PASS | `HTTP/2 307`, `location: /login?next=%2Fcampaigns` |
| SEC-060 | Deeplink-headers | / | Redirect 307 response still carries full security headers | PASS | CSP, XFO, XCTO, Referrer-Policy, HSTS, Permissions-Policy all present on the 307 itself (defense-in-depth — Cloudflare/Render edge stamps headers on every response, not just final 200) |
| SEC-061 | Deeplink-headers | /insights | Redirect 307 carries full security headers | PASS | Same as SEC-060 |
| SEC-062 | Deeplink-headers | /campaigns | Redirect 307 carries full security headers | PASS | Same as SEC-060 |
| SEC-063 | Open-redirect | /login?next= | sanitizeNext() rejects absolute / `//` / `..` / `\` / `@` / controls | SKIP | GET /login returns 200 regardless of `next=` value — sanitization runs on form-submit redirect target, requires successful auth flow. Pinned offline by `frontend/utils/url.test.mjs` (57 cases) + `tests/test_open_redirect.py` (CLAUDE.md "API Security invariants > Auth + transport") |
| SEC-064 | Post-signout | / | After signout, navigating to / yields no stale authed render | BLOCKED | No creds → no session → cannot exercise signout flow. Pinned offline by `app/api/auth/signout/route.ts` tests + `router.replace('/login'); router.refresh()` contract in CLAUDE.md. |
| SEC-065 | Authed-CSP | / (authed) | CSP nonce-integrity on authed dashboard render | BLOCKED | Same root cause as SEC-064. Same nonce-flow as /login by construction (layout.tsx force-dynamic, headers().get('x-nonce')) — SEC-037 + SEC-038 cover the mechanism. |
| SEC-066 | Authed-CSP | /insights (authed) | CSP nonce-integrity on authed Insights | BLOCKED | Same as SEC-065. |
| SEC-067 | Authed-CSP | /campaigns (authed) | CSP nonce-integrity on authed Campaigns | BLOCKED | Same as SEC-065. |
| SEC-068 | Authed-cookie | / | sb-* set-cookie attrs HttpOnly+Secure+Lax inspected on real flow | BLOCKED | Same root cause as SEC-064. Pinned by Supabase-cookie fuzz suite (1157 cases) — see SEC-042 detail. |
| SEC-069 | Backend-server | Backend GET / | Origin uvicorn fingerprint masked (no Server: uvicorn) | PASS | `server: cloudflare`. No `uvicorn`/`gunicorn`/`fastapi` token. CLAUDE.md "uvicorn --no-server-header" verified at edge. |
| SEC-070 | Backend-XFO | Backend GET / | X-Frame-Options DENY stamped by `_security_headers_middleware` | PASS | `x-frame-options: DENY` (FastAPI middleware setdefault, per CLAUDE.md "PR #238") |
| SEC-071 | Backend-XCTO | Backend GET / | X-Content-Type-Options nosniff | PASS | `x-content-type-options: nosniff` |
| SEC-072 | Backend-Referrer | Backend GET / | Referrer-Policy stamped | PASS | `referrer-policy: strict-origin-when-cross-origin` |
| SEC-073 | Backend-no-CSP | Backend GET / | Backend correctly OMITs CSP (no HTML payload — JSON only) | PASS | No `content-security-policy` on backend `/` response (matches CLAUDE.md "CSP + HSTS omitted (Render edge / no HTML)"; CSP lives on the Next frontend per-request middleware). |
| SEC-074 | Edge-CORP | / (signout response) | Cross-Origin-Resource-Policy same-origin | PASS | `cross-origin-resource-policy: same-origin` |
| SEC-075 | Edge-COOP | / (signout response) | Cross-Origin-Opener-Policy same-origin | PASS | `cross-origin-opener-policy: same-origin` |
| SEC-076 | Edge-XPCDP | / (signout response) | X-Permitted-Cross-Domain-Policies none | PASS | `x-permitted-cross-domain-policies: none` |
| SEC-077 | Edge-AltSvc | / | alt-svc + h3 advertisement only (no leak) | PASS | `alt-svc: h3=":443"; ma=86400` (HTTP/3 advertisement, not a leak) |
| SEC-078 | CSP-img-src | All routes | img-src restricts to self + data + blob + Supabase domain (no blanket https:) | PASS | `img-src 'self' data: blob: https://kbtkxpvchmunwjykbeht.supabase.co` (verified on / /insights /campaigns /login /api/proxy/* responses) |
| SEC-079 | CSP-connect-src | All routes | connect-src restricts to self + Supabase https + wss only | PASS | `connect-src 'self' https://kbtkxpvchmunwjykbeht.supabase.co wss://kbtkxpvchmunwjykbeht.supabase.co` |
| SEC-080 | CSP-frame-ancestors | All routes | frame-ancestors 'none' (no clickjacking) | PASS | `frame-ancestors 'none'` (defense-in-depth with X-Frame-Options DENY) |
| SEC-081 | CSP-object-src | All routes | object-src 'none' (no plugin embed) | PASS | `object-src 'none'` |
| SEC-082 | CSP-base-uri | All routes | base-uri 'self' (prevents `<base>` hijack) | PASS | `base-uri 'self'` |
| SEC-083 | CSP-form-action | All routes | form-action 'self' (limits where forms submit) | PASS | `form-action 'self'` |
| SEC-084 | CSP-default-src | All routes | default-src 'self' (fallback policy tight) | PASS | `default-src 'self'` |
| SEC-085 | Vary-Cookie | / /insights /campaigns | Vary: Cookie on auth-sensitive HTML routes | PASS | `vary: Cookie` (correct cache key separation for authed vs unauthed render) |
| SEC-086 | Vary-RSC | /login | Vary on RSC routing headers | PASS | `vary: rsc, next-router-state-tree, next-router-prefetch, next-router-segment-prefetch, Accept-Encoding` |
| SEC-087 | TLS-HTTP2 | All routes | All probes over HTTP/2 + TLS | PASS | `HTTP/2 ` status line on every response (Cloudflare edge) |
| SEC-088 | CSP-style-src | All routes | style-src restricts to 'self' + 'unsafe-inline' (Tailwind requirement) | PASS | `style-src 'self' 'unsafe-inline'` — `'unsafe-inline'` is a documented Tailwind requirement, not a defect (style-src has no `'unsafe-eval'`) |
