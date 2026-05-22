import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ensureProtocol } from './url.mjs'

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
