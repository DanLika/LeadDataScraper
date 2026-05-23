# Phase D — Backport bookbed-website headers to LDS frontend

**Status:** Plan only. Do NOT execute until LDS CI is green (else regression of
the new gates can't be distinguished from drift). Estimated effort once
unblocked: ~30 minutes + test-suite extension.

**Source of plan:** [`docs/bookbed-crossover.md`](bookbed-crossover.md) §Phase D
(Section 2.1 row "object-src + base-uri + form-action" + "COOP/CORP/Cross-Domain-Policies").

**Why this is Phase D (not Phase A):**
LDS is *behind* bookbed-website on a small set of defense-in-depth headers,
but every one is a low-risk addition (no functional change) — strictly
hardening with zero new attack surface. None block any user flow today, so
the work is informational only until a regression test exists to gate it.

---

## Gap inventory (verified 2026-05-23 against current `main`)

### Static headers — owned by `frontend/next.config.ts::baseHeaders`

| Header | LDS today | bookbed-website | Action |
|---|---|---|---|
| `Cross-Origin-Opener-Policy` | ❌ unset | `same-origin` | ✅ add |
| `Cross-Origin-Resource-Policy` | ❌ unset | `same-site` | ✅ add |
| `X-Permitted-Cross-Domain-Policies` | ❌ unset | `none` | ✅ add |
| `X-DNS-Prefetch-Control` | ❌ unset | `on` | ⚠️ optional — perf-leaning header, not security; skip unless we want it. |
| `X-Frame-Options: DENY` | ✅ already set | same | no-op |
| `X-Content-Type-Options: nosniff` | ✅ already set | same | no-op |
| `Referrer-Policy: strict-origin-when-cross-origin` | ✅ already set | same | no-op |
| `Permissions-Policy` (camera/mic/geo + more) | ✅ set, narrower | ✅ set, broader | ⚠️ consider expanding LDS list to match (see below). |
| `Strict-Transport-Security` | ✅ prod-gated, `max-age=63072000; includeSubDomains; preload` | ✅ `max-age=31536000; includeSubDomains; preload` | LDS is *stronger* (2y vs 1y) — keep LDS's value. |
| `poweredByHeader: false` | ✅ set | ✅ set | no-op |

### CSP directives — owned by `frontend/proxy.ts` (per-request, nonce-aware)

| Directive | LDS today | bookbed-website | Action |
|---|---|---|---|
| `default-src 'self'` | ✅ | ✅ | no-op |
| `script-src` | ✅ `'self' 'nonce-...' 'strict-dynamic'` (per-request) | `'self' 'unsafe-inline'` static (SSG can't use nonces) | divergent by design — LDS is stronger. |
| `style-src 'self' 'unsafe-inline'` | ✅ | ✅ + `https://fonts.googleapis.com` + Tawk | LDS allowlist tighter — keep. |
| `connect-src` | ✅ Supabase URL + wss | broader (Mux/Tawk/Vercel/Vitals) | divergent — both correct for their needs. |
| `img-src` | `'self' data: blob: <SUPABASE_URL>` | `'self' data: blob: https: https://*.tawk.to` | divergent. |
| `frame-ancestors 'none'` | ✅ | ✅ | no-op |
| `object-src 'none'` | ✅ | ✅ | no-op |
| `base-uri 'self'` | ✅ | ✅ | no-op |
| `form-action 'self'` | ✅ | ✅ + `mailto:` | LDS has no mailto forms — keep `'self'` only. |
| `manifest-src 'self'` | ❌ unset | ✅ | ✅ add (PWA manifest tightening) |
| `worker-src 'self' blob:` | ❌ unset | ✅ | ✅ add (web-worker / service-worker tightening) |
| `upgrade-insecure-requests` | ❌ unset | ✅ | ✅ add (auto-rewrites http:// subresource URLs to https://) |
| `media-src` | ❌ unset | ✅ Mux + blob | ⚠️ skip — LDS has no video; opening a gap-free hole. |
| `frame-src` | ❌ unset (frame-ancestors handles inbound) | ✅ Tawk + view.bookbed.io | ⚠️ skip — LDS doesn't embed iframes. |

---

## Why the picks (one line each)

- **COOP `same-origin`** — Splits browsing-context group. Mitigates Spectre-class
  cross-origin attacks + protects against `window.opener` leaks. Zero functional
  cost when the page doesn't `window.open` to attacker-controlled origins.
- **CORP `same-site`** — Prevents cross-site embedding of *this site's* resources
  in another origin. Pairs naturally with COOP.
- **X-Permitted-Cross-Domain-Policies `none`** — Closes the
  Flash / Acrobat / Silverlight `crossdomain.xml` legacy. Pure win — defeats
  a class of fingerprinting + content-injection paths via dead plugins.
- **manifest-src `'self'`** — PWA manifest tightening. LDS has no manifest
  today, but the directive defaults to `default-src 'self'` (which is set);
  making it explicit + restrictive future-proofs.
- **worker-src `'self' blob:`** — Web/service-worker scope tightening. LDS
  doesn't ship a service worker today, but `blob:` is also where a sandboxed
  iframe-spawned worker would live; allowing `'self' blob:` keeps Next.js
  RSC's internal worker creation supported while excluding cross-origin
  worker scripts.
- **upgrade-insecure-requests** — Belt-and-braces alongside HSTS: auto-rewrites
  any accidental `http://` subresource URL to `https://` before issuing the
  request. Defends against an editor mistake that lands `<img src="http://...">`
  on a page.

---

## Implementation steps (when unblocked)

### Step 1 — Static headers (`frontend/next.config.ts`)

Extend `baseHeaders` with the three new entries. Insert AFTER `Permissions-Policy`
(keeps related cross-origin policies grouped):

```typescript
const baseHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Phase D backport from bookbed-website (defense in depth).
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Cross-Origin-Resource-Policy", value: "same-site" },
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
];
```

### Step 2 — CSP directives (`frontend/proxy.ts`)

Locate the CSP string assembly. Add three new directives to the joined array:

```typescript
"manifest-src 'self'",
"worker-src 'self' blob:",
"upgrade-insecure-requests",
```

Place them after `frame-ancestors 'none'` (alphabetical-ish + groups
restrictive defaults together).

### Step 3 — Lock-in tests

Extend `tests/test_security_defenses.py` with a `TestPhaseDHeaders` class
(use the existing Playwright + static-curl pattern in that file):

- Assert `Cross-Origin-Opener-Policy: same-origin` on the response to a
  same-origin GET to `/login`.
- Assert `Cross-Origin-Resource-Policy: same-site` on the same response.
- Assert `X-Permitted-Cross-Domain-Policies: none` on the same response.
- Parse the per-request CSP from a fresh `/` GET; assert each new directive
  is present as a token.

These are static-header / single-header-line assertions — cheap to add and
fast to run. No live DB or Supabase context needed.

### Step 4 — CLAUDE.md docs touch

Add one bullet under "Browser security headers" in `CLAUDE.md` (or the
docs/security-invariants.md split-out file, whichever owns the section by
the time this runs) covering the three new static headers + three new CSP
directives. Pattern: same one-line summary other defenses use.

---

## What this PR is NOT

- NOT a backport of bookbed-website's `media-src` or `frame-src` allowlists —
  LDS has no media playback and no iframes.
- NOT a backport of bookbed-website's `'unsafe-inline'` script-src — LDS uses
  a stronger per-request nonce + `'strict-dynamic'` flow. Keep that.
- NOT a relaxation of LDS's tight Supabase-only `connect-src` to match
  bookbed-website's broader Mux/Tawk allowlist. Stays scoped to the LDS
  surface.

---

## Risk

Three known false-positive risks. None block the addition:

1. **COOP same-origin + popup-based OAuth.** If LDS ever adds an OAuth flow
   via popup window (`window.open`), the `noopener` reference of the popup
   to the opener will break window-passing handshakes. Mitigation: relax
   to `same-origin-allow-popups` only on routes that need it.
2. **CORP same-site + cross-origin asset use.** If LDS images are ever
   hot-linked into a partner widget, they will 404 cross-site. Mitigation:
   serve those assets from a separate path with a relaxed CORP.
3. **upgrade-insecure-requests + intentional http:// resource.** If any code
   genuinely needs a plain-HTTP subresource (cron-scraped page preview?),
   the browser will silently upgrade and the resource will 404. Mitigation:
   none needed — that resource was already broken under HSTS.

These are all low-probability under current LDS shape (no OAuth popup, no
cross-site hot-linking, no plain-HTTP subresources). Re-evaluate if any
of those shape changes land.

---

## Closing the loop

After Step 3 lands, mark this doc DONE in `docs/bookbed-crossover.md`'s
phased action checklist (Phase D — currently labeled "Optional"). The work
graduates from "documented gap" to "closed gap."
