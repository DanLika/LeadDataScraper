# Phase D — Backport bookbed-website response headers to LDS

**Status:** PLAN — do not execute. The crossover doc
([`bookbed-crossover.md`](bookbed-crossover.md) §2.1) flagged that
bookbed-website is ahead of LDS on six response headers. This file
specifies the exact diff so the eventual `chore/backport-headers-from-bb`
PR is a copy job, not a research job.

**Created:** 2026-05-23 — output of B.4 of the 2026-05-23 session.
**Re-verify before executing:** bookbed-website moves fast; the CSP
directive list may have shifted since 2026-05-23.

---

## What the crossover doc claimed vs. what's actually true

The original gap-analysis claimed LDS was behind on **six** patterns:
COOP, CORP, `X-Permitted-Cross-Domain-Policies`, `object-src 'none'`,
`base-uri 'self'`, `form-action 'self' mailto:`. Direct read of
`frontend/proxy.ts` shows three of those are **already implemented**:

| Doc claim | LDS reality |
|---|---|
| `object-src 'none'` missing | ✅ already in `proxy.ts::buildCsp` line 19 |
| `base-uri 'self'` missing | ✅ already in `proxy.ts::buildCsp` line 16 |
| `form-action 'self' mailto:` missing | ⚠️ partially — `proxy.ts` line 17 has `form-action 'self'` (no `mailto:`) |

Net real gap = 3 missing static headers + 1 CSP directive narrowing
+ broader Permissions-Policy + supplementary CSP directives.

---

## Source-of-truth files

| File | Role |
|---|---|
| `frontend/next.config.ts::baseHeaders` | static headers stamped on every response |
| `frontend/proxy.ts::buildCsp` | per-request CSP (nonce-bound) |
| `frontend/next.config.ts::pageNoCacheHeaders` | cache headers on HTML routes — orthogonal, do NOT touch |

The "static" headers below land in `baseHeaders` (one source-of-truth
already in the file). The CSP directive add lands in `buildCsp` inside
`proxy.ts`. Keep the per-request boundary intact — do NOT migrate CSP
back into `next.config.ts`.

---

## Diff — header by header

### A. Static response headers (`baseHeaders` in `next.config.ts`)

| Header | bookbed-website value | LDS state | Recommended LDS value |
|---|---|---|---|
| Cross-Origin-Opener-Policy | `same-origin` | ❌ missing | `same-origin` |
| Cross-Origin-Resource-Policy | `same-site` | ❌ missing | `same-site` |
| X-Permitted-Cross-Domain-Policies | `none` | ❌ missing | `none` |
| X-DNS-Prefetch-Control | `on` | ❌ missing | `on` *(optional — perf hint, not security)* |
| Permissions-Policy | 11 directives | partial (3) | broaden to 11 |

Patch shape:

```typescript
const baseHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: [
      "geolocation=()",
      "microphone=()",
      "camera=()",
      "payment=()",
      "usb=()",
      "magnetometer=()",
      "gyroscope=()",
      "accelerometer=()",
      "fullscreen=(self)",
      "interest-cohort=()",   // FLoC opt-out
      "browsing-topics=()",   // Topics API opt-out
    ].join(", "),
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-site" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
  // X-DNS-Prefetch-Control is a perf hint, not security — defer to a
  // separate PR if/when prefetch behaviour ever matters.
];
```

### B. CSP directives (`buildCsp` in `proxy.ts`)

| Directive | bookbed-website value | LDS state | Recommended LDS value |
|---|---|---|---|
| `form-action` | `'self' mailto:` | `'self'` only | `'self' mailto:` *(matches mailto-link CSP semantics already used in outreach modal)* |
| `manifest-src` | `'self'` | missing | `'self'` |
| `worker-src` | `'self' blob:` | missing | `'self' blob:` *(Next.js may emit blob workers for dynamic imports)* |
| `media-src` | `'self' …` | missing | skip — LDS has no video/audio |
| `frame-src` | `'self' https://*.tawk.to https://view.bookbed.io` | missing | skip — LDS embeds no iframes |
| `upgrade-insecure-requests` | present | missing | add — defense-in-depth |

Patch shape (in `buildCsp`):

```typescript
const directives = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self' mailto:",            // CHANGED: + mailto:
  "frame-ancestors 'none'",
  "object-src 'none'",
  "manifest-src 'self'",                    // NEW
  "worker-src 'self' blob:",                // NEW
  "upgrade-insecure-requests",              // NEW (no value; directive flag)
  "style-src 'self' 'unsafe-inline'",
  // … existing script-src / img-src / font-src / connect-src unchanged
];
```

---

## Risk per change

### Low risk (additive headers, no observable impact today)
- COOP `same-origin` — only matters for `window.opener` access across
  origins; LDS doesn't pop windows.
- CORP `same-site` — only matters for cross-origin embedders; LDS
  enforces `frame-ancestors 'none'` already.
- `X-Permitted-Cross-Domain-Policies: none` — disables Adobe
  Flash/Acrobat cross-domain policy files. LDS doesn't ship either.
- Permissions-Policy expansion — additional `*=()` opt-outs cannot
  break anything that already worked.

### Medium risk (CSP changes — must verify in `npm run start`)
- `form-action 'self' mailto:` — LDS DOES use `mailto:` deep links
  in the outreach modal (`frontend/app/page.tsx`). The current
  `form-action 'self'` directive does NOT block `<a href="mailto:">`
  navigation (that's `navigate-to`, not `form-action`), but it does
  block any form that POSTs to a `mailto:` action. No such form
  exists today. Adding `mailto:` is a no-op now but future-proofs
  the modal if it ever upgrades from `<a>` to `<form>`.
- `manifest-src 'self'` — LDS has no `manifest.json` today. Adding
  the directive locks `<link rel="manifest">` to same-origin — if
  someone later wires PWA support, they must serve the manifest
  from `/`. No regression today.
- `worker-src 'self' blob:` — Next.js 16's webpack splits client
  bundles into chunks; some import patterns emit `new Worker(url)`
  using a blob: URL. Without the directive, those workers fail
  silently. **VERIFY:** `npm run build && npm run start`, check
  DevTools console for `Refused to create a worker` violations.
- `upgrade-insecure-requests` — auto-upgrades any `http:` URL
  the browser would otherwise issue. **VERIFY:** local dev
  (`http://localhost:3000`) still works — the directive applies
  only to non-same-origin requests. If the directive bubbles into
  CSP for localhost, dev breaks. Pin in prod env only via the
  existing `NODE_ENV === 'production'` branch.

### High risk (none for this PR)

---

## Test extension

Add a single assertion block to `tests/test_security_defenses.py`:

```python
import re

def test_static_headers_match_bb_parity(client):
    """LDS frontend should advertise the same defense-in-depth headers
    as bookbed-website (per docs/phase-d-header-backport-plan.md)."""
    response = client.get("/")
    headers = response.headers
    assert headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert headers.get("Cross-Origin-Resource-Policy") == "same-site"
    assert headers.get("X-Permitted-Cross-Domain-Policies") == "none"
    # Permissions-Policy must include the FLoC + Topics opt-outs.
    pp = headers.get("Permissions-Policy", "")
    assert "interest-cohort=()" in pp
    assert "browsing-topics=()" in pp

def test_csp_supplementary_directives(client):
    response = client.get("/")
    csp = response.headers.get("Content-Security-Policy", "")
    assert "manifest-src 'self'" in csp
    assert "worker-src 'self' blob:" in csp
    assert "upgrade-insecure-requests" in csp
    assert "form-action 'self' mailto:" in csp
```

**Caveat.** `test_security_defenses.py` today tests the FastAPI
backend, not the Next.js frontend. The HTTP layer this assertion
targets is the **Next.js dev/prod server**, not the FastAPI app.
Two options:

1. Move these to a **Playwright e2e test** in
   `tests/e2e/security-headers.spec.ts` — fires against a running
   `npm run start` instance. Matches the existing
   `tests/test_open_redirect.py` pattern (env-gated, opt-in).
2. Write a **pure-Node assertion** against `npm run build` output
   that scans the compiled `.next/static-manifest.json` for the
   header config — purely static, no server needed.

Recommend option 1 — closer to real browser behaviour, catches the
medium-risk worker-src / upgrade-insecure-requests regressions an
introspection-only test would miss.

---

## Execution sequence

When the operator opens `chore/backport-headers-from-bb`:

1. Edit `frontend/next.config.ts::baseHeaders` per Section A patch.
2. Edit `frontend/proxy.ts::buildCsp` per Section B patch.
3. Add the new test (option 1 above) under `tests/e2e/`.
4. Local verify: `cd frontend && npm run build && npm run start`
   then `curl -sI http://localhost:3000/ | grep -iE 'cross-origin|permissions|x-permitted'`.
5. Browser smoke: open `http://localhost:3000/`, sign in, navigate
   every authed route, watch DevTools console for `Refused to …`
   CSP violations. Particular attention to the lazy-loaded chunks
   (`HealthChart`, `AIChat`, `LeadTable`) — `worker-src` regressions
   only manifest under code-splitting.
6. Run existing security test suite:
   `pytest tests/security/ tests/test_security_defenses.py`.
7. PR title: `security(headers): backport bookbed-website parity (COOP / CORP / X-Permitted-Cross-Domain-Policies + CSP supplementary)`.

**Estimated effort:** 30 min file edit + 30 min in-browser verify
+ 30 min test author = ~90 min single-session.

---

## Out of scope (do NOT port)

- `media-src` directive — LDS embeds no video/audio.
- `frame-src` directive — LDS embeds no iframes.
- bookbed-website's `'unsafe-eval'` in `script-src` (dev only) —
  LDS already has it in `proxy.ts::buildCsp` line 25 dev branch.
- bookbed-website's vercel-scripts / tawk.to / mux.com origins —
  LDS uses Supabase + Sentry; the connect-src allowlist is already
  correct for LDS's third parties (`SUPABASE_URL` + Sentry
  `/monitoring` tunnel).
- `images.formats: ["image/avif", "image/webp"]` — LDS doesn't
  serve user-uploaded images; carry-over from bookbed-website's
  property-photo surface that doesn't apply.

---

## Verification checklist (run before claiming done)

- [ ] `curl -sI http://localhost:3000/ | grep Cross-Origin-Opener-Policy` returns `same-origin`.
- [ ] `curl -sI http://localhost:3000/ | grep Permissions-Policy` includes `interest-cohort=()`.
- [ ] DevTools console: 0 `Refused to ...` CSP violations on dashboard, insights, campaigns pages.
- [ ] `frontend/utils/url.test.mjs` + `frontend/utils/supabase/cookie-floor.test.mjs` + `cookie-floor-fuzz.test.mjs` all green.
- [ ] `pytest tests/security/ tests/test_security_defenses.py` exits 0.
- [ ] (If e2e test added) `RUN_HEADERS_E2E=1 pytest tests/e2e/security-headers.spec.ts` green against `npm run start`.

If any of the above fails: **do not merge**. The headers are
defense-in-depth and not worth shipping a regression for.
