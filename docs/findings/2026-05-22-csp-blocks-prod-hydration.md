# CSP `script-src 'self'` bricks the prod build — React never hydrates

**Severity:** Sev-1 (login impossible in `npm run start`; every authed page is
blocked in production).
**Discovered:** 2026-05-22, during live chrome-devtools-mcp testing.
**Affected build:** Next.js 16.2.6, `frontend/next.config.ts:17`.
**Status: RESOLVED 2026-05-22 on branch `fix/csp-nonce-rsc-hydration`** —
recommended fix path (per-request nonce + `'strict-dynamic'`) implemented
across three load-bearing files. Verified end-to-end via chrome-devtools-mcp:
15 streamed scripts now carry the per-request nonce, login form renders,
hydration succeeds, dashboard reaches `Pipeline Intelligence`. Landing
patches:
- `frontend/proxy.ts` — generates 16-byte base64 nonce per request, sets
  `x-nonce` on a NEW `Headers` object passed via
  `NextResponse.next({ request: { headers } })` and the matching
  `Content-Security-Policy` response header.
- `frontend/utils/supabase/middleware.ts` — `updateSession` now accepts
  the request-header override and threads it into `NextResponse.next`.
- `frontend/app/layout.tsx` — `export const dynamic = 'force-dynamic'`
  + `(await headers()).get('x-nonce')` to register the `headers()`
  dependency. Without `force-dynamic`, Next.js statically prerenders and
  the request-time nonce never reaches the renderer.
- `frontend/next.config.ts` — static `Content-Security-Policy` line
  removed; the other static security headers (HSTS, X-Frame-Options,
  etc.) stay.

Architecture detail in `CLAUDE.md` ("Browser security headers" section).
The historical analysis below is kept for posterity.

## Repro

```
cd frontend
npm run build && npm run start
# open http://localhost:3000/login
```

Page renders the `<title>` but the login form never appears. DevTools console
shows six `Content Security Policy directive 'script-src 'self'` errors and
one `Uncaught (in promise)` from the blocked bootstrap. `document.body`
contains 6376 bytes of HTML but `document.querySelectorAll('form').length === 0`
and `document.querySelectorAll('input').length === 0`.

## Root cause

`frontend/next.config.ts:6-27` sets the production CSP to:

```
default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none';
object-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self';
img-src 'self' data: blob: https://kbtkxpvchmunwjykbeht.supabase.co;
font-src 'self' data:;
connect-src 'self' https://...supabase.co wss://...supabase.co
```

Next 16's App Router streams the RSC payload to the browser as **inline**
`<script>self.__next_f.push([…])</script>` blocks. The serialized HTML
contains, verbatim:

```html
<script>(self.__next_f=self.__next_f||[]).push([0])</script>
<script>self.__next_f.push([1,"1:\"$Sreact.fragment\"\n2:I[46329,…"])</script>
…
<template data-dgst="BAILOUT_TO_CLIENT_SIDE_RENDERING"></template>
```

`script-src 'self'` rejects every one of these — they have no nonce, no
hash, and no `'unsafe-inline'`. The async `/_next/static/chunks/*.js`
bundles load fine (same-origin), but they never get the streamed payload
they need to hydrate against, so the render tree stays at the framework
shells (`$L2`, `$L3`, `$L4`) and the page is dead.

The comment at `next.config.ts:13` — *"CSS is the only inline asset;
scripts run from /_next/static (same-origin)"* — was true under
pages-router / older app-router builds, but is **no longer true** in
Next 16 RSC.

## Why CI never caught it

`.github/workflows/e2e.yml:30` reads `E2E_BASE_URL` from a GitHub Actions
secret and never starts a local Next.js server — `playwright.config.ts`
has no `webServer` block, and the file header explicitly tells the
developer to start the server themselves. So:

- **Local developer runs**: per `playwright.config.ts:5`, the dev pattern
  is `npm run dev` (lax CSP — `script-src 'self' 'unsafe-eval'
  'unsafe-inline'`). Hydration succeeds; bug is invisible. The 2026-05-21
  smoke-flow run documented in CLAUDE.md almost certainly fell here.
- **CI E2E job**: hits whatever URL the operator set in `E2E_BASE_URL`
  secret. If that's a Render-hosted preview built from a previous commit
  (before the current `script-src 'self'` line landed) or with a
  different edge CSP, the bug is masked. Worth checking what
  `E2E_BASE_URL` currently points at.
- **The specific local path that bricks**: `npm run build && npm run start`
  on the developer's machine — and that path is in **no** CI workflow.

Recommend adding a `webServer` block to `playwright.config.ts` that uses
`npm run start` (with a precondition `npm run build`) for at least one
test project so the prod-mode hydration path is exercised in CI.

## Downstream blockers in this session

The following queued tasks **cannot run** until this is fixed:

- Lighthouse audit on `/`, `/insights`, `/campaigns` → login required, login
  is broken.
- Cold-context Web Vitals (FCP/LCP/INP/CLS/TTFB) → same.
- Network waterfall for `/` → same.

No bypass via cookie injection helps — even after a session cookie is
placed in the jar, `/` re-renders client-side via RSC and hits the same
CSP wall.

## Fix options

In order of recommendation:

### (A) Nonce + `'strict-dynamic'` (canonical Next.js prod CSP)

Standard Next.js pattern, documented at
https://nextjs.org/docs/app/guides/content-security-policy.

The injection point is **`frontend/proxy.ts`** (the Next 16 convention
file already in the repo — it wraps `utils/supabase/middleware.ts`).
Do **not** create a parallel `frontend/middleware.ts` — Next 16 errors
on duplicate convention files, per the warning in CLAUDE.md.

1. In `frontend/proxy.ts` (before calling `updateSession`), generate
   `const nonce = crypto.randomUUID().replace(/-/g, '')` per request,
   write it into a request header (`x-nonce`) on the inbound request,
   then set CSP on the outbound response:
   `script-src 'self' 'nonce-<nonce>' 'strict-dynamic'`.
2. Remove the `Content-Security-Policy` line from `next.config.ts`
   (the static `headers()` block) — the per-request CSP set in
   `proxy.ts` supersedes it. Keep the other static headers
   (HSTS, X-Frame-Options, etc.) in `next.config.ts`.
3. In `app/layout.tsx`, read the nonce via
   `(await headers()).get('x-nonce')` and pass it to `<Script>` tags.
   Next.js stamps the nonce onto its own streamed inline `__next_f`
   blocks automatically when it sees one set on the request.

This preserves the strictness of `'self'` for static script URLs while
letting the runtime emit dynamic inline blocks under a nonce — the
hardening *intended* by the original config, working as designed.

### (B) `'strict-dynamic'` alone (weaker, simpler)

`script-src 'self' 'strict-dynamic'` lets any script already loaded via
the `'self'` allowlist load further scripts. For Next.js this often suffices
because the bootstrap chunk is the trust root. **Caveat:** the inline
`__next_f` payload tags are not themselves loaded via the bootstrap; they
are their own top-level inline scripts and still need a nonce. Likely
won't fix it on its own — test before committing.

### (C) `'unsafe-inline'`

Two-line change in `next.config.ts`. Defeats CSP's XSS hardening for
scripts. Acceptable only as a temporary unblock while (A) lands.

### (D) Switch RSC delivery off

Set every page that doesn't need RSC to `'use client'` and pre-render
statically. Architectural change, doesn't address the underlying CSP
question. Not recommended.

## Other things noticed while here

- `next.config.ts:13` comment must be updated whichever path is chosen,
  to stop the next reader hitting the same bricked-prod surprise.
- The `BAILOUT_TO_CLIENT_SIDE_RENDERING` template in the response payload
  is expected — `app/page.tsx` is a `'use client'` component wrapped in
  Suspense per CLAUDE.md's Next 16 contract — and is **not** related to
  this bug. Don't chase it.
- The session cookies set on `/login` POST never get a chance to be set,
  because the form never renders and the action never fires. So the
  earlier `signInWithPassword` HTTP-200 against the Supabase REST endpoint
  remains the only working auth path — useful for backend smoke tests, but
  not for any test that drives the real UI.

## Recommended next steps

1. File this finding as an issue.
2. Implement (A) on a branch; verify `npm run build && npm run start`
   reaches `/login` → form renders → submit → `/` dashboard renders.
3. Add a `npm run start`-based smoke step to `preview-smoke.yml` (or a
   new `prod-smoke.yml`) that asserts `document.querySelectorAll('input').length > 0`
   on `/login` after a 5 s grace — catches future CSP regressions.
4. Once (A) is in, re-run the queued live testing tasks:
   - Lighthouse on 3 routes
   - Web Vitals real-user-style (cold + throttled)
   - Network waterfall + warm-cache assertions
