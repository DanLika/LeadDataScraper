// Pins the inbound-header strip invariant for
// `app/api/proxy/[...path]/route.ts`. Run via `npm test` (node --test).
//
// Threat model: an authenticated browser caller, or a server-side caller
// reaching the proxy with a forged Origin, supplies their own
// `X-Admin-Token` (or `X-API-Key`) header. Without the strip, that value
// is copied into the headers forwarded to FastAPI. Backend rejection on a
// wrong-value `X-Admin-Token` is the practical gate, but any future
// handler bug that loosens that check, or any future route that requires
// the header but is missed in the proxy's `ADMIN_TOKEN_PATHS` allowlist,
// would silently inherit attacker-controlled auth. Strip closes the trust
// boundary.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import {
  HOP_BY_HOP,
  STRIPPED_AUTH,
  shouldDropInboundHeader,
} from './proxyHeaderFilter.mjs';

test('STRIPPED_AUTH covers both auth headers the proxy injects', () => {
  assert.ok(STRIPPED_AUTH.has('x-api-key'), 'must strip x-api-key');
  assert.ok(STRIPPED_AUTH.has('x-admin-token'), 'must strip x-admin-token');
  assert.equal(STRIPPED_AUTH.size, 2, 'no extra entries — keep list audit-friendly');
});

test('HOP_BY_HOP keeps the RFC 7230 hop-by-hop names + trust-laundering XFF set', () => {
  for (const expected of [
    'host', 'connection', 'content-length', 'transfer-encoding',
    'upgrade', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'accept-encoding',
    'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto',
    'x-real-ip', 'forwarded',
  ]) {
    assert.ok(HOP_BY_HOP.has(expected), `HOP_BY_HOP missing ${expected}`);
  }
});

test('shouldDropInboundHeader is case-insensitive on the name (matches Fetch Headers contract)', () => {
  // Browsers / fetch normalise to lowercase, but defense-in-depth — the
  // helper must reject any mixed-case variant a non-browser client sends.
  for (const cased of ['X-Admin-Token', 'x-admin-TOKEN', 'X-API-KEY', 'X-Api-Key']) {
    assert.ok(shouldDropInboundHeader(cased), `should drop ${cased}`);
  }
  // Sanity — a benign header survives.
  assert.equal(shouldDropInboundHeader('Content-Type'), false);
  assert.equal(shouldDropInboundHeader('User-Agent'), false);
});

test('forward-header simulation drops client-supplied X-Admin-Token before server re-injection', () => {
  // Simulates the inner loop of `forward()` in
  // `app/api/proxy/[...path]/route.ts` so a regression that re-introduces
  // the bare `!HOP_BY_HOP.has(...)` filter (and lets x-admin-token through)
  // fails this test.
  const inbound = new Headers();
  inbound.set('Content-Type', 'application/json');
  inbound.set('Cookie', 'sb-session=opaque');
  inbound.set('X-Admin-Token', 'attacker-supplied');
  inbound.set('X-API-Key', 'attacker-supplied');
  inbound.set('Host', 'example.invalid');

  const forwarded = new Headers();
  inbound.forEach((value, key) => {
    if (shouldDropInboundHeader(key)) return;
    forwarded.set(key, value);
  });

  assert.equal(forwarded.get('x-admin-token'), null, 'x-admin-token must be stripped');
  assert.equal(forwarded.get('x-api-key'), null, 'x-api-key must be stripped');
  assert.equal(forwarded.get('host'), null, 'Host (hop-by-hop) must be stripped');
  assert.equal(forwarded.get('content-type'), 'application/json', 'benign headers survive');
  assert.equal(forwarded.get('cookie'), 'sb-session=opaque', 'Cookie survives (auth session)');

  // Then the route re-injects server-side. The injected value MUST win
  // even if (hypothetically) a future regression skipped the strip.
  forwarded.set('X-API-Key', 'SERVER_SIDE_SECRET');
  assert.equal(
    forwarded.get('x-api-key'),
    'SERVER_SIDE_SECRET',
    'server injection wins regardless of strip',
  );
});
