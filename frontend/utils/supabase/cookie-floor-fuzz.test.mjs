/**
 * Adversarial fuzz against `hardenCookieOptions`. The sibling
 * `cookie-floor.test.mjs` pins the happy-path invariants — this file
 * targets the failure modes that a degraded / malicious Supabase client
 * could trigger:
 *
 *   - Cookie without `Secure` flag in prod HTTPS → MUST be rewritten
 *     to `secure: true`.
 *   - Cookie with `SameSite=None` → MUST collapse to `Lax`.
 *   - Cookie missing `HttpOnly` → MUST be added.
 *   - Cookie `Domain` set wider than current origin (`.com`, leading
 *     dot, attacker-host) → DOCUMENTED CURRENT BEHAVIOR: floor passes
 *     domain through unchanged. The same-origin defense lives at the
 *     `Set-Cookie` parser layer (browser refuses `Domain=.com`). A
 *     belt-and-braces narrow-domain check would harden further; left
 *     as a TODO with a failing assertion the operator can promote.
 *   - `__Host-` prefix violations — DOCUMENTED: the `__Host-` semantics
 *     are enforced by the BROWSER (no Domain attr, Path=/, Secure).
 *     The floor doesn't know the cookie name; left as a TODO.
 *
 * The malformed-options cases (mostly type confusion) are full coverage:
 * every input must yield a `{sameSite, httpOnly, secure}` triple the
 * browser parser will accept, never crash the call site.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hardenCookieOptions } from './cookie-floor.mjs'


// ── Production-mode invariants on every adversarial input ────────────

const PROD_ADVERSARIAL_INPUTS = [
  { sameSite: 'None',     httpOnly: false,  secure: false },
  { sameSite: 'none',     httpOnly: false,  secure: false },
  { sameSite: 'NONE',     httpOnly: false,  secure: false },
  { sameSite: 'lax',      httpOnly: false,  secure: false },
  { sameSite: '',         httpOnly: false,  secure: false },
  { sameSite: null,       httpOnly: null,   secure: null },
  { sameSite: undefined,  httpOnly: undefined, secure: undefined },
  { sameSite: 'lax',      httpOnly: 0,      secure: 0 },
  { sameSite: 'invalid',  httpOnly: false,  secure: false },
  { sameSite: 123,        httpOnly: 'no',   secure: 'no' },
  { sameSite: ['lax'],    httpOnly: {},     secure: [] },
  { sameSite: 'lax',      httpOnly: false,  secure: false, maxAge: -1 },
]

for (const [i, input] of PROD_ADVERSARIAL_INPUTS.entries()) {
  test(`prod-floor invariants hold on adversarial input #${i} ${JSON.stringify(input)}`, () => {
    const out = hardenCookieOptions(input, true)
    assert.equal(out.httpOnly, true, 'httpOnly must be true in prod')
    assert.equal(out.secure, true, 'secure must be true in prod')
    assert.ok(
      out.sameSite === 'lax' || out.sameSite === 'strict',
      `sameSite must be lax|strict, got ${JSON.stringify(out.sameSite)}`,
    )
  })
}


// ── Dev mode (HTTPS not guaranteed) — secure mirrors SDK request,
// but httpOnly + sameSite floor still applies. ──

const DEV_ADVERSARIAL_INPUTS = [
  { sameSite: 'none', httpOnly: false, secure: false },
  { sameSite: 'none', httpOnly: false, secure: true },
  {},
  null,
  undefined,
]

for (const [i, input] of DEV_ADVERSARIAL_INPUTS.entries()) {
  test(`dev-floor: httpOnly + sameSite still floored, secure mirrors SDK #${i}`, () => {
    const out = hardenCookieOptions(input, false)
    assert.equal(out.httpOnly, true, 'httpOnly always true regardless of mode')
    assert.ok(
      out.sameSite === 'lax' || out.sameSite === 'strict',
      `sameSite must be lax|strict, got ${JSON.stringify(out.sameSite)}`,
    )
    // In dev, secure mirrors `Boolean(options?.secure)` — false/null/missing
    // collapses to false; true stays true.
    assert.equal(out.secure, Boolean(input?.secure))
  })
}


// ── Strict-preservation: SDK choosing Strict must survive every shape ──

const STRICT_VARIANTS = [
  { sameSite: 'strict' },
  { sameSite: 'Strict' },
  { sameSite: 'STRICT' },
  { sameSite: 'strict', httpOnly: false, secure: false },
]

for (const variant of STRICT_VARIANTS) {
  test(`SameSite=Strict (${JSON.stringify(variant.sameSite)}) preserved through floor`, () => {
    const out = hardenCookieOptions(variant, true)
    assert.equal(out.sameSite, 'strict')
  })
}


// ── Non-protected fields pass through verbatim ──

test('maxAge / path / domain / expires NOT mutated by floor', () => {
  const input = {
    sameSite: 'lax',
    httpOnly: false,
    secure: false,
    maxAge: 7200,
    path: '/api',
    domain: '.example.com',
    expires: new Date('2030-01-01').toUTCString(),
    priority: 'high',
  }
  const out = hardenCookieOptions(input, true)
  assert.equal(out.maxAge, 7200)
  assert.equal(out.path, '/api')
  assert.equal(out.domain, '.example.com')
  assert.equal(out.expires, new Date('2030-01-01').toUTCString())
  assert.equal(out.priority, 'high')
})


// ── No mutation of caller's object — purity check ──

test('hardenCookieOptions does not mutate input object', () => {
  const input = { sameSite: 'none', httpOnly: false, secure: false }
  const snapshot = { ...input }
  hardenCookieOptions(input, true)
  assert.deepEqual(input, snapshot, 'input must not be mutated')
})


// ── Total-coverage scheme assertions: across the entire matrix of
// (sameSite, httpOnly, secure) inputs in prod, the output's
// `Set-Cookie`-relevant triple is ALWAYS browser-acceptable. ──

const SAMESITE_INPUTS = [undefined, null, '', 'lax', 'LAX', 'none', 'NONE',
                         'strict', 'STRICT', 'invalid', 'none; httpOnly',
                         42, [], {}]
const BOOL_INPUTS = [true, false, undefined, null, 0, 1, '', 'true', 'false']

let total = 0
for (const ss of SAMESITE_INPUTS) {
  for (const ho of BOOL_INPUTS) {
    for (const sc of BOOL_INPUTS) {
      total++
      test(`prod matrix #${total}: ss=${JSON.stringify(ss)} ho=${JSON.stringify(ho)} sc=${JSON.stringify(sc)}`, () => {
        const out = hardenCookieOptions(
          { sameSite: ss, httpOnly: ho, secure: sc }, true,
        )
        assert.equal(out.secure, true, 'prod must always emit Secure')
        assert.equal(out.httpOnly, true, 'prod must always emit HttpOnly')
        assert.ok(
          out.sameSite === 'lax' || out.sameSite === 'strict',
          `sameSite must be lax|strict, got ${JSON.stringify(out.sameSite)}`,
        )
      })
    }
  }
}


// ── Documented gaps. These tests use `.skip` to surface what the floor
// could harden against; promote them to live tests when the production
// code adds these defenses. ──

test.skip('TODO: Domain wider than current origin should be narrowed', () => {
  // `hardenCookieOptions` currently lets `domain: '.com'` or
  // `domain: 'evil.com'` pass through. The browser's cookie parser
  // would reject `.com` (TLD too broad) but a sibling-host cookie like
  // `.example.com` set from `app.example.com` is accepted. If the floor
  // ever moves to a context-aware variant (knows current host),
  // promote this test.
  const out = hardenCookieOptions(
    { sameSite: 'lax', domain: '.com' }, true,
  )
  // Expectation: floor strips invalid domain or narrows to host-only.
  assert.notEqual(out.domain, '.com')
})

test.skip("TODO: __Host- prefixed cookies must have Path=/ and no Domain", () => {
  // The `__Host-` prefix is a browser-enforced constraint: cookie name
  // starts with `__Host-` → must be Secure, Path=/, no Domain. The
  // floor doesn't see the cookie NAME today (it only gets options).
  // If the API surface grows to include the name, validate the prefix
  // rules here.
})
