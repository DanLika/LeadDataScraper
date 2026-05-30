/**
 * Adversarial fuzz against `hardenCookieOptions`. The sibling
 * `cookie-floor.test.mjs` pins the happy-path invariants — this file
 * targets the failure modes that a degraded / malicious Supabase client
 * could trigger:
 *
 *   - Cookie without `Secure` flag → MUST be rewritten to `secure: true`.
 *     The floor is now unconditional (no NODE_ENV dependency); CI / deploy
 *     misconfig can't ship cookies without Secure.
 *   - Cookie with `SameSite=None` → MUST collapse to `Lax`.
 *   - Cookie missing `HttpOnly` → MUST be added.
 *   - Cookie `Domain` set wider than current origin (`.com`, leading
 *     dot, attacker-host) → MUST be narrowed to the current host or stripped.
 *   - `__Host-` prefix violations → MUST be stripped of Domain and given Path=/.
 *
 * The malformed-options cases (mostly type confusion) are full coverage:
 * every input must yield a `{sameSite, httpOnly, secure}` triple the
 * browser parser will accept, never crash the call site.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hardenCookieOptions } from './cookie-floor.mjs'


// ── Floor invariants on every adversarial input ──────────────────────

const ADVERSARIAL_INPUTS = [
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
  {},
  null,
  undefined,
]

for (const [i, input] of ADVERSARIAL_INPUTS.entries()) {
  test(`floor invariants hold on adversarial input #${i} ${JSON.stringify(input)}`, () => {
    const out = hardenCookieOptions(input)
    assert.equal(out.httpOnly, true, 'httpOnly must always be true')
    assert.equal(out.secure, true, 'secure must always be true (no NODE_ENV dependency)')
    assert.ok(
      out.sameSite === 'lax' || out.sameSite === 'strict',
      `sameSite must be lax|strict, got ${JSON.stringify(out.sameSite)}`,
    )
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
    const out = hardenCookieOptions(variant)
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
  const out = hardenCookieOptions(input)
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
  hardenCookieOptions(input)
  assert.deepEqual(input, snapshot, 'input must not be mutated')
})


// ── Total-coverage scheme assertions: across the entire matrix of
// (sameSite, httpOnly, secure) inputs, the output's `Set-Cookie`-relevant
// triple is ALWAYS browser-acceptable. ──

const SAMESITE_INPUTS = [undefined, null, '', 'lax', 'LAX', 'none', 'NONE',
                         'strict', 'STRICT', 'invalid', 'none; httpOnly',
                         42, [], {}]
const BOOL_INPUTS = [true, false, undefined, null, 0, 1, '', 'true', 'false']

let total = 0
for (const ss of SAMESITE_INPUTS) {
  for (const ho of BOOL_INPUTS) {
    for (const sc of BOOL_INPUTS) {
      total++
      test(`matrix #${total}: ss=${JSON.stringify(ss)} ho=${JSON.stringify(ho)} sc=${JSON.stringify(sc)}`, () => {
        const out = hardenCookieOptions(
          { sameSite: ss, httpOnly: ho, secure: sc },
        )
        assert.equal(out.secure, true, 'must always emit Secure')
        assert.equal(out.httpOnly, true, 'must always emit HttpOnly')
        assert.ok(
          out.sameSite === 'lax' || out.sameSite === 'strict',
          `sameSite must be lax|strict, got ${JSON.stringify(out.sameSite)}`,
        )
      })
    }
  }
}


// ── Advanced hardening features ──

test('Domain wider than current origin or unrelated should be stripped', () => {
  // Broad TLD is stripped
  const out1 = hardenCookieOptions({ sameSite: 'lax', domain: '.com' }, 'sb-test', 'app.example.com')
  assert.equal(out1.domain, undefined)

  // Sibling host is stripped
  const out2 = hardenCookieOptions({ sameSite: 'lax', domain: 'evil.com' }, 'sb-test', 'app.example.com')
  assert.equal(out2.domain, undefined)

  // Single label domains are stripped unless localhost
  const out3 = hardenCookieOptions({ sameSite: 'lax', domain: 'invalid' }, 'sb-test', 'app.example.com')
  assert.equal(out3.domain, undefined)

  // Parent domain without leading dot is preserved
  const out4 = hardenCookieOptions({ sameSite: 'lax', domain: 'example.com' }, 'sb-test', 'app.example.com')
  assert.equal(out4.domain, 'example.com')

  // Parent domain with leading dot is preserved
  const out5 = hardenCookieOptions({ sameSite: 'lax', domain: '.example.com' }, 'sb-test', 'app.example.com')
  assert.equal(out5.domain, '.example.com')

  // Exact match is preserved
  const out6 = hardenCookieOptions({ sameSite: 'lax', domain: 'app.example.com' }, 'sb-test', 'app.example.com')
  assert.equal(out6.domain, 'app.example.com')

  // Port in currentHost is stripped before check
  const out7 = hardenCookieOptions({ sameSite: 'lax', domain: 'app.example.com' }, 'sb-test', 'app.example.com:3000')
  assert.equal(out7.domain, 'app.example.com')
})

test("__Host- prefixed cookies must have Path=/ and no Domain", () => {
  const out = hardenCookieOptions(
    { sameSite: 'lax', domain: 'example.com', path: '/foo' },
    '__Host-session',
    'app.example.com'
  )
  assert.equal(out.domain, undefined)
  assert.equal(out.path, '/')
  assert.equal(out.secure, true)
})
