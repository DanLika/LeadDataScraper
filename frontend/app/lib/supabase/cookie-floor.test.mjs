import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hardenCookieOptions } from './cookie-floor.mjs'

test('forces httpOnly=true even when SDK passes false', () => {
  const out = hardenCookieOptions({ httpOnly: false, sameSite: 'lax' })
  assert.equal(out.httpOnly, true)
})

test('forces httpOnly=true when SDK omits it entirely', () => {
  const out = hardenCookieOptions({ sameSite: 'lax' })
  assert.equal(out.httpOnly, true)
})

test('forces httpOnly=true when options is undefined (defensive)', () => {
  const out = hardenCookieOptions(undefined)
  assert.equal(out.httpOnly, true)
})

test('floors sameSite=none to lax', () => {
  const out = hardenCookieOptions({ sameSite: 'none' })
  assert.equal(out.sameSite, 'lax')
})

test('floors missing sameSite to lax', () => {
  const out = hardenCookieOptions({})
  assert.equal(out.sameSite, 'lax')
})

test('preserves sameSite=strict when SDK tightens', () => {
  const out = hardenCookieOptions({ sameSite: 'strict' })
  assert.equal(out.sameSite, 'strict')
})

test('preserves sameSite=strict case-insensitively', () => {
  const out = hardenCookieOptions({ sameSite: 'Strict' })
  assert.equal(out.sameSite, 'strict')
})

test('forces secure=true unconditionally', () => {
  // No NODE_ENV dependency. Localhost is a "trustworthy origin" per WHATWG
  // so dev still works; CI / deploy misconfig can't ship cookies without
  // Secure flag.
  const out = hardenCookieOptions({ secure: false })
  assert.equal(out.secure, true)
})

test('secure=true even when SDK passes undefined / missing', () => {
  assert.equal(hardenCookieOptions(undefined).secure, true)
  assert.equal(hardenCookieOptions({}).secure, true)
})

test('preserves non-protected SDK options like maxAge and path', () => {
  const out = hardenCookieOptions(
    { maxAge: 3600, path: '/api', domain: 'example.com' },
  )
  assert.equal(out.maxAge, 3600)
  assert.equal(out.path, '/api')
  assert.equal(out.domain, 'example.com')
})
