#!/usr/bin/env node
// Post-deploy auth + cookie-floor smoke. Catches SSR session bugs and
// cookie-floor regressions in production — the unit tests in
// frontend/app/lib/supabase/cookie-floor.test.mjs pin the helper; this
// pins the live response on real prod cookies.
//
// Required env:
//   FRONTEND_URL           https://<prod-host>
//   TEST_OPERATOR_EMAIL    throwaway test account (provisioned in Supabase)
//   TEST_PASSWORD          matching password
//   PROD_BACKEND_URL       optional; only used if a sign-out probe needs it
// Optional:
//   SLACK_WEBHOOK_URL      alert on failure

import { chromium } from 'playwright';

const {
  FRONTEND_URL,
  TEST_OPERATOR_EMAIL,
  TEST_PASSWORD,
  SLACK_WEBHOOK_URL,
} = process.env;

const REQUIRED = ['FRONTEND_URL', 'TEST_OPERATOR_EMAIL', 'TEST_PASSWORD'];
for (const k of REQUIRED) {
  if (!process.env[k]) {
    console.error(`MISSING_ENV ${k}`);
    process.exit(2);
  }
}

const failures = [];
function record(name, ok, detail) {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failures.push({ name, detail });
}

const expectedDomain = new URL(FRONTEND_URL).hostname;

async function postSlack(text) {
  if (!SLACK_WEBHOOK_URL) return;
  try {
    await fetch(SLACK_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch {
    /* best-effort */
  }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
try {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));

  // 1. Sign in with the throwaway account.
  await page.goto(`${FRONTEND_URL}/login`, { waitUntil: 'networkidle' });
  await page.fill('input[name="email"]', TEST_OPERATOR_EMAIL);
  await page.fill('input[name="password"]', TEST_PASSWORD);
  await Promise.all([
    page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 }),
    page.click('button[type="submit"]'),
  ]);
  record('sign_in', !page.url().includes('/login'), `url=${page.url()}`);

  // 2. Inspect Supabase session cookies (sb-* prefix).
  const cookies = await ctx.cookies();
  const sbCookies = cookies.filter((c) => c.name.startsWith('sb-'));
  record('session_cookies_present', sbCookies.length > 0, `count=${sbCookies.length}`);

  for (const c of sbCookies) {
    // cookie-floor.mjs hard-sets sameSite='lax' (or 'strict' if Supabase
    // requested it), httpOnly=true, and secure=true in prod.
    record(`cookie_${c.name}_httponly`, c.httpOnly === true, `httpOnly=${c.httpOnly}`);
    record(
      `cookie_${c.name}_samesite`,
      c.sameSite === 'Lax' || c.sameSite === 'Strict',
      `sameSite=${c.sameSite}`,
    );
    // In prod (HTTPS), secure MUST be true.
    if (FRONTEND_URL.startsWith('https://')) {
      record(`cookie_${c.name}_secure`, c.secure === true, `secure=${c.secure}`);
    }
    // Domain alignment — the cookie must be set for the prod host, not
    // an unrelated domain. Allow a leading dot for "broad" cookies.
    const domainOk =
      c.domain === expectedDomain ||
      c.domain === `.${expectedDomain}` ||
      expectedDomain.endsWith(c.domain.replace(/^\./, ''));
    record(`cookie_${c.name}_domain`, domainOk, `domain=${c.domain} expected≈${expectedDomain}`);
  }

  // 3. Authenticated load — / must render cleanly, no JS errors.
  const homeResp = await page.goto(`${FRONTEND_URL}/`, { waitUntil: 'networkidle' });
  record('home_loads', (homeResp?.status() ?? 0) < 400, `status=${homeResp?.status()}`);
  record(
    'home_no_js_errors',
    consoleErrors.length === 0,
    consoleErrors.length ? consoleErrors.slice(0, 3).join(' | ') : 'clean',
  );

  // 4. Hard reload — session must persist (cookies survive, getUser passes).
  await page.reload({ waitUntil: 'networkidle' });
  record('hard_reload_still_authed', !page.url().includes('/login'), `url=${page.url()}`);

  // 5. Sign out — cookie cleared, redirected to /login.
  const signoutResp = await page.evaluate(async () => {
    const r = await fetch('/api/auth/signout', { method: 'POST', credentials: 'include' });
    return r.status;
  });
  record('signout_endpoint_ok', signoutResp === 200, `status=${signoutResp}`);

  // After signout, navigation to / should bounce to /login. Supabase
  // signOut clears the sb-* cookies via Set-Cookie; the browser also
  // strips them once the server says they're expired.
  await page.goto(`${FRONTEND_URL}/`, { waitUntil: 'networkidle' });
  record('post_signout_redirects_login', page.url().includes('/login'), `url=${page.url()}`);

  const afterCookies = (await ctx.cookies()).filter((c) => c.name.startsWith('sb-') && c.value);
  record('post_signout_cookies_cleared', afterCookies.length === 0, `remaining=${afterCookies.length}`);
} finally {
  await browser.close().catch(() => {});
}

if (failures.length > 0) {
  await postSlack(
    `:warning: LeadDataScraper auth smoke failed — ${failures.length} check${failures.length === 1 ? '' : 's'}.\n` +
      failures.map((f) => `• *${f.name}*: ${f.detail || ''}`).join('\n'),
  );
  console.error(`\nAUTH_SMOKE_FAILED count=${failures.length}`);
  process.exit(1);
}
console.log('\nAUTH_SMOKE_PASSED');
