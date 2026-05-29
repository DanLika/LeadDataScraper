import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ensureProtocol, sanitizeNext } from './url.mjs'

/**
 * Core security invariant: `ensureProtocol` output is ALWAYS either the
 * empty string or an `http(s)://` URL — never a `javascript:`, `data:`,
 * `file:`, `vbscript:` (etc.) scheme that a browser would execute or
 * dereference dangerously from an `<a href>`.
 */
function assertSchemeSafe(result) {
  assert.equal(typeof result, 'string')
  if (result !== '') {
    assert.match(result, /^https?:\/\//, `output must be http(s) or "", got: ${result}`)
  }
}

// ── safe values: http/https pass through, bare host gets https:// ──
test('keeps a valid https URL', () => {
  assert.equal(ensureProtocol('https://example.com/'), 'https://example.com/')
})
test('keeps a valid http URL', () => {
  assert.equal(ensureProtocol('http://example.com/'), 'http://example.com/')
})
test('prepends https:// to a bare host', () => {
  assert.equal(ensureProtocol('example.com'), 'https://example.com/')
})
test('prepends https:// to a host with path', () => {
  assert.equal(ensureProtocol('example.com/path'), 'https://example.com/path')
})

// ── XSS scheme guard: dangerous schemes never survive as the protocol ──
test('javascript: scheme → blocked (empty)', () => {
  assert.equal(ensureProtocol('javascript:alert(1)'), '')
})
test('JavaScript: scheme blocked case-insensitively', () => {
  assert.equal(ensureProtocol('JavaScript:alert(1)'), '')
})
test('javascript: with leading whitespace → blocked', () => {
  assert.equal(ensureProtocol('  javascript:alert(1)'), '')
})
test('data: scheme → blocked', () => {
  assert.equal(ensureProtocol('data:text/html,<script>alert(1)</script>'), '')
})
test('vbscript: scheme → blocked', () => {
  assert.equal(ensureProtocol('vbscript:msgbox(1)'), '')
})
test('pre-prefixed https://javascript: payload → blocked (invalid port)', () => {
  assert.equal(ensureProtocol('https://javascript:alert(1)'), '')
})

// every dangerous-scheme input, whatever the exact output, must satisfy
// the scheme-safe invariant — output is "" or http(s), never the payload
for (const payload of [
  'javascript:alert(1)',
  'JAVASCRIPT:alert(1)',
  'data:text/html,<script>x</script>',
  'file:///etc/passwd',
  'vbscript:msgbox(1)',
  '  \t javascript:alert(1)',
  'jaVAscript:alert(document.cookie)',
]) {
  test(`scheme-safe invariant holds for: ${JSON.stringify(payload)}`, () => {
    const out = ensureProtocol(payload)
    assertSchemeSafe(out)
    assert.ok(!out.toLowerCase().startsWith('javascript:'), 'never javascript:')
    assert.ok(!out.toLowerCase().startsWith('data:'), 'never data:')
    assert.ok(!out.toLowerCase().startsWith('vbscript:'), 'never vbscript:')
    assert.ok(!out.toLowerCase().startsWith('file:'), 'never file:')
  })
}

// ── empty / nullish / non-string input ──
test('empty string → ""', () => {
  assert.equal(ensureProtocol(''), '')
})
test('whitespace-only → ""', () => {
  assert.equal(ensureProtocol('   '), '')
})
test('null → ""', () => {
  assert.equal(ensureProtocol(null), '')
})
test('undefined → ""', () => {
  assert.equal(ensureProtocol(undefined), '')
})
test('non-string input never throws + stays scheme-safe', () => {
  assert.doesNotThrow(() => ensureProtocol(12345))
  assertSchemeSafe(ensureProtocol(12345))
})

// ── garbage the URL parser rejects ──
test('garbage with spaces → ""', () => {
  assert.equal(ensureProtocol('not a url at all'), '')
})

// ─────────────────────────── sanitizeNext ───────────────────────────
// Open-redirect guard for the login ?next= param.

test('sanitizeNext: plain same-origin path passes', () => {
  assert.equal(sanitizeNext('/'), '/')
  assert.equal(sanitizeNext('/insights'), '/insights')
  assert.equal(sanitizeNext('/campaigns?view=audited'), '/campaigns?view=audited')
})

test('sanitizeNext: nullish / empty → /', () => {
  assert.equal(sanitizeNext(null), '/')
  assert.equal(sanitizeNext(undefined), '/')
  assert.equal(sanitizeNext(''), '/')
})

test('sanitizeNext: non-string → /', () => {
  assert.equal(sanitizeNext(123), '/')
  assert.equal(sanitizeNext({}), '/')
})

test('sanitizeNext: over-length (>512) → /', () => {
  assert.equal(sanitizeNext('/' + 'a'.repeat(600)), '/')
})

// the open-redirect attack matrix — every one must collapse to '/'
for (const evil of [
  '//evil.com',                       // protocol-relative
  '//evil.com/path',
  'https://evil.com',                 // absolute — no leading '/'
  'http://evil.com',
  'javascript:alert(1)',              // scheme — no leading '/'
  'JAVASCRIPT:alert(1)',              // case variant
  'data:text/html,<script>alert(1)</script>', // data: URI
  '/\t//evil.com',                    // control char → parser strips → //evil
  '/\n//evil.com',
  '/\r//evil.com',
  '/\\evil.com',                      // backslash → normalised to /
  '/@evil.com/foo',                   // userinfo phishing-display form
  '/path:8080',                       // ':' excluded from allowlist
  'relative-no-slash',                // must start with '/'
  '/ space',                          // raw space not allowed

  // ── percent-encoded host-swap / traversal — caught by the decode-once
  // pass; the regex alone allows '%' so these would otherwise slip through.
  '/dashboard%2f%2fevil.com',         // decoded → /dashboard//evil.com
  '/dashboard%2F%2Fevil.com',         // case variant
  '/%2e%2e/evil.com',                 // decoded → /../evil.com (traversal)
  '/%2E%2E/evil.com',                 // case variant
  '/foo%5cevil.com',                  // decoded backslash
  '/foo%5Cevil.com',
  '%2F%2Fevil.com',                   // no leading '/'
  '/foo%00bar',                       // NUL after decode
  '/foo%0devil',                      // CR after decode
  '/foo%0aevil',                      // LF after decode
  '/foo%09evil',                      // TAB after decode
  '/foo%7Fevil',                      // DEL after decode

  // ── doubly-encoded — first decode-pass yields `%2f%2fevil.com`, which
  // still matches the allowlist but a downstream consumer that decodes
  // again would resolve to `//evil.com`. The decode-once layer doesn't
  // catch this on its own, but the leading `/` shape keeps the value
  // same-origin under WHATWG resolution → covered separately by the
  // 'allowed pass-through' tests below.
]) {
  test(`sanitizeNext: open-redirect payload ${JSON.stringify(evil)} → /`, () => {
    assert.equal(sanitizeNext(evil), '/')
  })
}

// the over-length payload check has its own test above. Belt-and-braces:
// a deeply-nested traversal must also collapse.
test('sanitizeNext: deeply-nested encoded traversal → /', () => {
  assert.equal(sanitizeNext('/%2e%2e/%2e%2e/%2e%2e/etc/passwd'), '/')
})

// non-throwing on malformed percent-encoding — `decodeURIComponent` throws
// on a stray `%` not followed by two hex chars; the guard catches it.
test('sanitizeNext: malformed percent-encoding → / (no throw)', () => {
  assert.doesNotThrow(() => sanitizeNext('/foo%ZZ'))
  assert.equal(sanitizeNext('/foo%ZZ'), '/')
  assert.equal(sanitizeNext('/foo%'), '/')
  assert.equal(sanitizeNext('/foo%2'), '/')
})

// legit values that should still pass — guard against over-tightening.
test('sanitizeNext: legit paths still pass after hardening', () => {
  assert.equal(sanitizeNext('/insights'), '/insights')
  assert.equal(sanitizeNext('/campaigns?view=audited'), '/campaigns?view=audited')
  assert.equal(sanitizeNext('/leads?segment=high-risk&page=2'),
    '/leads?segment=high-risk&page=2')
  assert.equal(sanitizeNext('/path#anchor'), '/path#anchor')
  // Percent-encoded chars that don't form a dangerous decoded sequence still pass.
  assert.equal(sanitizeNext('/search?q=hello%20world'), '/search?q=hello%20world')
})
