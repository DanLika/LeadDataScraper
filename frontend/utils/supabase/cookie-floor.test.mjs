import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hardenCookieOptions } from './cookie-floor.mjs'

test('forces httpOnly=true even when SDK passes false', () => {
  const out = hardenCookieOptions({ httpOnly: false, sameSite: 'lax' }, true)
  assert.equal(out.httpOnly, true)
})

test('forces httpOnly=true when SDK omits it entirely', () => {
  const out = hardenCookieOptions({ sameSite: 'lax' }, true)
  assert.equal(out.httpOnly, true)
})

test('forces httpOnly=true when options is undefined (defensive)', () => {
  const out = hardenCookieOptions(undefined, true)
  assert.equal(out.httpOnly, true)
})

test('floors sameSite=none to lax', () => {
  const out = hardenCookieOptions({ sameSite: 'none' }, true)
  assert.equal(out.sameSite, 'lax')
})

test('floors missing sameSite to lax', () => {
  const out = hardenCookieOptions({}, true)
  assert.equal(out.sameSite, 'lax')
})

test('preserves sameSite=strict when SDK tightens', () => {
  const out = hardenCookieOptions({ sameSite: 'strict' }, true)
  assert.equal(out.sameSite, 'strict')
})

test('preserves sameSite=strict case-insensitively', () => {
  const out = hardenCookieOptions({ sameSite: 'Strict' }, true)
  assert.equal(out.sameSite, 'strict')
})

test('forces secure=true in production', () => {
  const out = hardenCookieOptions({ secure: false }, true)
  assert.equal(out.secure, true)
})

test('keeps secure as SDK requested in dev', () => {
  const dev1 = hardenCookieOptions({ secure: false }, false)
  assert.equal(dev1.secure, false)
  const dev2 = hardenCookieOptions({ secure: true }, false)
  assert.equal(dev2.secure, true)
})

test('preserves non-protected SDK options like maxAge and path', () => {
  const out = hardenCookieOptions(
    { maxAge: 3600, path: '/api', domain: 'example.com' },
    true,
  )
  assert.equal(out.maxAge, 3600)
  assert.equal(out.path, '/api')
  assert.equal(out.domain, 'example.com')
})
