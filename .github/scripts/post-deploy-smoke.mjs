#!/usr/bin/env node
// Post-deploy smoke runner. Five checks against the backend + frontend.
// Exits 0 on all-pass, non-zero on any failure. The wrapping workflow
// inspects the exit code and triggers a Render rollback on non-zero.

import { chromium } from 'playwright';

const {
  BACKEND_URL,
  FRONTEND_URL,
  API_SECRET_KEY,
} = process.env;

const REQUIRED = ['BACKEND_URL', 'FRONTEND_URL', 'API_SECRET_KEY'];
for (const key of REQUIRED) {
  if (!process.env[key]) {
    console.error(`MISSING_ENV ${key}`);
    process.exit(2);
  }
}

const TIMEOUT_MS = 15_000;
const failures = [];

function record(name, ok, detail) {
  const status = ok ? 'PASS' : 'FAIL';
  console.log(`[${status}] ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failures.push({ name, detail });
}

async function fetchWithTimeout(url, init = {}, ms = TIMEOUT_MS) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(t);
  }
}

// 1. Backend liveness — GET / returns 200 {status: "ok"}.
async function checkLiveness() {
  try {
    const res = await fetchWithTimeout(`${BACKEND_URL}/`);
    if (res.status !== 200) {
      record('backend_liveness', false, `status=${res.status}`);
      return;
    }
    const body = await res.json().catch(() => null);
    record('backend_liveness', body?.status === 'ok', `body=${JSON.stringify(body)}`);
  } catch (e) {
    record('backend_liveness', false, `error=${e.message}`);
  }
}

// 2. Schema health — GET /health/schema with X-API-Key returns drift=false.
async function checkSchema() {
  try {
    const res = await fetchWithTimeout(`${BACKEND_URL}/health/schema`, {
      headers: { 'X-API-Key': API_SECRET_KEY },
    });
    if (res.status !== 200) {
      record('schema_health', false, `status=${res.status}`);
      return;
    }
    const body = await res.json();
    record('schema_health', body.drift === false, `drift=${body.drift} missing=${body.missing_columns_count}`);
  } catch (e) {
    record('schema_health', false, `error=${e.message}`);
  }
}

// 3. AI ask — POST /ask returns 200 with a usable plan / answer.
async function checkAsk() {
  try {
    const res = await fetchWithTimeout(`${BACKEND_URL}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_SECRET_KEY,
      },
      body: JSON.stringify({ instruction: { text: "what's my lead count" } }),
    });
    if (res.status !== 200) {
      record('ask_endpoint', false, `status=${res.status}`);
      return;
    }
    const body = await res.json();
    // `/ask` autoexec returns the result envelope (task/answer/summary).
    // Plan-card returns include a top-level `task` field too. Either is fine
    // — failure shape would be { error: "..." } or missing both.
    const ok = !body.error && (body.task || body.answer || body.message);
    record('ask_endpoint', Boolean(ok), `task=${body.task} answerPresent=${Boolean(body.answer)}`);
  } catch (e) {
    record('ask_endpoint', false, `error=${e.message}`);
  }
}

// 4 + 5. Frontend /login loads cleanly, CSP header present.
async function checkFrontend() {
  let browser;
  try {
    // Header check first — cheap, no browser needed for CSP.
    const headRes = await fetchWithTimeout(`${FRONTEND_URL}/login`, { redirect: 'manual' });
    const csp = headRes.headers.get('content-security-policy');
    record('csp_header', Boolean(csp && csp.includes("script-src")), `len=${csp?.length ?? 0}`);

    browser = await chromium.launch({ args: ['--no-sandbox'] });
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    page.on('console', (m) => {
      if (m.type() === 'error') consoleErrors.push(m.text());
    });

    const resp = await page.goto(`${FRONTEND_URL}/login`, { waitUntil: 'networkidle', timeout: TIMEOUT_MS });
    const status = resp?.status() ?? 0;
    const loaded = status >= 200 && status < 400;
    record('frontend_login_loads', loaded, `status=${status}`);

    const total = pageErrors.length + consoleErrors.length;
    record(
      'frontend_no_js_errors',
      total === 0,
      total ? `pageErrors=${JSON.stringify(pageErrors)} consoleErrors=${JSON.stringify(consoleErrors)}` : 'clean',
    );
  } catch (e) {
    record('frontend_login_loads', false, `error=${e.message}`);
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

await checkLiveness();
await checkSchema();
await checkAsk();
await checkFrontend();

if (failures.length > 0) {
  console.error(`\nSMOKE_FAILED count=${failures.length}`);
  console.error(JSON.stringify(failures, null, 2));
  process.exit(1);
}
console.log('\nSMOKE_PASSED');
