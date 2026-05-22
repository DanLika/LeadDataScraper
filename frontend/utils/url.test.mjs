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
  '/\t//evil.com',                    // control char → parser strips → //evil
  '/\n//evil.com',
  '/\r//evil.com',
  '/\\evil.com',                      // backslash → normalised to /
  '/@evil.com/foo',                   // userinfo phishing-display form
  '/path:8080',                       // ':' excluded from allowlist
  'relative-no-slash',                // must start with '/'
  '/ space',                          // raw space not allowed
]) {
  test(`sanitizeNext: open-redirect payload ${JSON.stringify(evil)} → /`, () => {
    assert.equal(sanitizeNext(evil), '/')
  })
}
