// Pins the single-tenant invariant for
// `app/api/proxy/[...path]/route.ts`. Run via `npm test` (node --test).
//
// Threat model: see module docstring in `singleTenantGuard.mjs`. The
// gap this closes: backend's lifespan tenancy check catches a 2nd
// Supabase user provisioned BEFORE boot; a user provisioned AFTER boot
// reaches every backend endpoint via the proxy with the server-side
// X-API-Key attached. The proxy is the only place that has the per-
// request Supabase session JWT (via `supabase.auth.getUser()`), so the
// guard must live here.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { checkSingleTenant } from './singleTenantGuard.mjs';

const OPERATOR = 'operator@example.com';

test('opt-in: empty expectedEmail allows any user (including null)', () => {
  for (const user of [null, { email: 'anything@example.com' }, { email: null }, { email: '' }]) {
    const r = checkSingleTenant({ user, expectedEmail: '' });
    assert.deepEqual(r, { allowed: true });
  }
});

test('opt-in: whitespace-only expectedEmail is treated as unset', () => {
  const r = checkSingleTenant({ user: { email: 'attacker@example.com' }, expectedEmail: '   ' });
  assert.deepEqual(r, { allowed: true });
});

test('match: exact case allows', () => {
  assert.deepEqual(
    checkSingleTenant({ user: { email: OPERATOR }, expectedEmail: OPERATOR }),
    { allowed: true },
  );
});

test('match: case-insensitive on actual', () => {
  assert.deepEqual(
    checkSingleTenant({ user: { email: 'Operator@Example.COM' }, expectedEmail: OPERATOR }),
    { allowed: true },
  );
});

test('match: case-insensitive on expected', () => {
  assert.deepEqual(
    checkSingleTenant({ user: { email: OPERATOR }, expectedEmail: 'OPERATOR@example.com' }),
    { allowed: true },
  );
});

test('match: whitespace-tolerant on both sides', () => {
  assert.deepEqual(
    checkSingleTenant({ user: { email: '  operator@example.com  ' }, expectedEmail: `\t${OPERATOR}\n` }),
    { allowed: true },
  );
});

test('reject: different operator email → 403 single_tenant_violation', () => {
  const r = checkSingleTenant({
    user: { email: 'attacker@example.com' },
    expectedEmail: OPERATOR,
  });
  assert.deepEqual(r, { allowed: false, status: 403, errorCode: 'single_tenant_violation' });
});

test('reject: lookalike unicode does NOT smuggle past case-fold', () => {
  // Cyrillic 'о' vs Latin 'o' — different codepoints; must reject.
  const r = checkSingleTenant({
    user: { email: 'оperator@example.com' },
    expectedEmail: OPERATOR,
  });
  assert.equal(r.allowed, false);
  assert.equal(r.status, 403);
});

test('reject: user with no email (deleted? phone-auth?) → 403', () => {
  const r = checkSingleTenant({ user: { email: null }, expectedEmail: OPERATOR });
  assert.deepEqual(r, { allowed: false, status: 403, errorCode: 'single_tenant_violation' });
});

test('reject: user object exists but empty email → 403', () => {
  const r = checkSingleTenant({ user: { email: '' }, expectedEmail: OPERATOR });
  assert.deepEqual(r, { allowed: false, status: 403, errorCode: 'single_tenant_violation' });
});

test('reject: null user when enforcement enabled → 403', () => {
  // In the route, the !user check fires first with 401. This guard's
  // contract is: when expectedEmail is set AND the user email doesn't
  // match, return 403. A null user has no email, so it's a mismatch.
  // The route's 401-before-403 ordering keeps end-users seeing the
  // semantically correct status for unauthenticated traffic.
  const r = checkSingleTenant({ user: null, expectedEmail: OPERATOR });
  assert.deepEqual(r, { allowed: false, status: 403, errorCode: 'single_tenant_violation' });
});

test('reject: substring is not a match (prefix attack)', () => {
  // attacker@example.com vs operator@example.com — the .com tail is
  // shared but the comparison is full-string equality, not endsWith.
  const r = checkSingleTenant({
    user: { email: 'attacker.operator@example.com' },
    expectedEmail: OPERATOR,
  });
  assert.equal(r.allowed, false);
});

test('reject: surrounding-whitespace value normalises only via trim, not by stripping internals', () => {
  // 'op erator@example.com' contains an internal space — must reject,
  // not silently strip and match.
  const r = checkSingleTenant({
    user: { email: 'op erator@example.com' },
    expectedEmail: OPERATOR,
  });
  assert.equal(r.allowed, false);
});
