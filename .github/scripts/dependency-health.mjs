#!/usr/bin/env node
// Dependency health probe — checks the four hard externals the backend
// can't function without: Gemini, Supabase (REST + service_role),
// Google Maps, and the Render API.
//
// Invocation: post-deploy-smoke workflow runs this BEFORE the code
// smoke. A failure here means an *external* blip (not a regression in
// our code), so the workflow MUST NOT roll back the deploy on a
// non-zero exit. Slack alert is posted inline so the operator knows
// which dep is down before the smoke output even lands.
//
// Required env: GEMINI_API_KEY, SUPABASE_URL, SUPABASE_ANON_KEY,
// SUPABASE_SERVICE_ROLE_KEY, RENDER_API_KEY, BACKEND_SERVICE_ID.
// BACKEND_SERVICE_ID is wired from the deploy event in the workflow;
// running standalone (e.g. under a future cron) would need it surfaced
// as a repo secret first.

const {
  GEMINI_API_KEY,
  GEMINI_TEST_MODEL,                 // optional, defaults below
  SUPABASE_URL,
  SUPABASE_ANON_KEY,
  SUPABASE_SERVICE_ROLE_KEY,
  RENDER_API_KEY,
  BACKEND_SERVICE_ID,
  SLACK_WEBHOOK_URL,
  ALERT_CONTEXT,                     // free-text label for the Slack message
} = process.env;

const REQUIRED = [
  'GEMINI_API_KEY',
  'SUPABASE_URL',
  'SUPABASE_ANON_KEY',
  'SUPABASE_SERVICE_ROLE_KEY',
  'RENDER_API_KEY',
  'BACKEND_SERVICE_ID',
];
for (const k of REQUIRED) {
  if (!process.env[k]) {
    console.error(`MISSING_ENV ${k}`);
    process.exit(2);
  }
}

const TIMEOUT_MS = 15_000;
const MODEL = GEMINI_TEST_MODEL || 'gemini-2.0-flash';

async function withTimeout(fn, ms = TIMEOUT_MS) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    return await fn(ctl.signal);
  } finally {
    clearTimeout(t);
  }
}

function ok(name) {
  return { name, ok: true };
}
function fail(name, reason) {
  return { name, ok: false, reason };
}

// 1. Gemini — POST a 5-token completion, assert 200 + non-empty text.
async function checkGemini() {
  try {
    return await withTimeout(async (signal) => {
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${encodeURIComponent(GEMINI_API_KEY)}`;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contents: [{ parts: [{ text: 'ping' }] }],
          generationConfig: { maxOutputTokens: 5 },
        }),
        signal,
      });
      if (res.status !== 200) {
        return fail('gemini', `status=${res.status} body=${(await res.text()).slice(0, 200)}`);
      }
      const body = await res.json();
      const text = body?.candidates?.[0]?.content?.parts?.[0]?.text ?? '';
      if (!text) {
        // Some safety blocks return 200 with no text — treat as healthy
        // ONLY if a finishReason is present (means model responded, just
        // refused content). Missing both = degraded.
        const finish = body?.candidates?.[0]?.finishReason;
        if (!finish) return fail('gemini', `200 but empty body=${JSON.stringify(body).slice(0, 200)}`);
      }
      return ok('gemini');
    });
  } catch (e) {
    return fail('gemini', e.name === 'AbortError' ? 'timeout' : e.message);
  }
}

// 2a. Supabase REST — anon key reachability. Project-level endpoint
// `/rest/v1/` returns 200 with a valid apikey regardless of table grants
// (RLS revocation on `leads`/`campaigns` would mask a `select` probe).
async function checkSupabaseRest() {
  try {
    return await withTimeout(async (signal) => {
      const res = await fetch(`${SUPABASE_URL}/rest/v1/`, {
        headers: { apikey: SUPABASE_ANON_KEY },
        signal,
      });
      if (res.status !== 200) return fail('supabase_rest', `status=${res.status}`);
      return ok('supabase_rest');
    });
  } catch (e) {
    return fail('supabase_rest', e.name === 'AbortError' ? 'timeout' : e.message);
  }
}

// 2b. Supabase service_role — actually read a row from `leads`. This is
// the path the backend uses (bypasses RLS via service_role). Returns
// 200 + array on success; 401/403 if the key has been rotated/revoked.
async function checkSupabaseServiceRole() {
  try {
    return await withTimeout(async (signal) => {
      const res = await fetch(`${SUPABASE_URL}/rest/v1/leads?select=unique_key&limit=1`, {
        headers: {
          apikey: SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
        },
        signal,
      });
      if (res.status !== 200) {
        return fail('supabase_service_role', `status=${res.status} body=${(await res.text()).slice(0, 200)}`);
      }
      const body = await res.json().catch(() => null);
      if (!Array.isArray(body)) return fail('supabase_service_role', `unexpected body shape`);
      return ok('supabase_service_role');
    });
  } catch (e) {
    return fail('supabase_service_role', e.name === 'AbortError' ? 'timeout' : e.message);
  }
}

// 3. Google Maps reachability — HEAD the maps URL the discovery_engine
// scraper navigates to. We're not authenticating, just verifying the
// endpoint resolves and serves. Maps occasionally 302 to a regional
// host, so accept any 2xx/3xx.
async function checkGoogleMaps() {
  try {
    return await withTimeout(async (signal) => {
      const res = await fetch('https://www.google.com/maps', {
        method: 'HEAD',
        redirect: 'manual',
        signal,
      });
      if (res.status < 200 || res.status >= 400) return fail('google_maps', `status=${res.status}`);
      return ok('google_maps');
    });
  } catch (e) {
    return fail('google_maps', e.name === 'AbortError' ? 'timeout' : e.message);
  }
}

// 4. Render API — used by post-deploy-smoke's rollback step. If this is
// down the rollback path fails silently right when we need it most, so
// alert preemptively.
async function checkRenderApi() {
  try {
    return await withTimeout(async (signal) => {
      const res = await fetch(`https://api.render.com/v1/services/${BACKEND_SERVICE_ID}`, {
        headers: {
          Authorization: `Bearer ${RENDER_API_KEY}`,
          Accept: 'application/json',
        },
        signal,
      });
      if (res.status !== 200) return fail('render_api', `status=${res.status}`);
      return ok('render_api');
    });
  } catch (e) {
    return fail('render_api', e.name === 'AbortError' ? 'timeout' : e.message);
  }
}

async function postSlack(text) {
  if (!SLACK_WEBHOOK_URL) {
    console.warn('SLACK_WEBHOOK_URL unset — skipping alert.');
    return;
  }
  try {
    const res = await fetch(SLACK_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) console.error(`Slack post failed status=${res.status}`);
  } catch (e) {
    console.error(`Slack post error: ${e.message}`);
  }
}

const results = await Promise.all([
  checkGemini(),
  checkSupabaseRest(),
  checkSupabaseServiceRole(),
  checkGoogleMaps(),
  checkRenderApi(),
]);

for (const r of results) {
  console.log(`[${r.ok ? 'PASS' : 'FAIL'}] ${r.name}${r.ok ? '' : ' — ' + r.reason}`);
}

const failures = results.filter((r) => !r.ok);
if (failures.length === 0) {
  console.log('\nALL_DEPS_HEALTHY');
  process.exit(0);
}

const ctx = ALERT_CONTEXT ? ` (${ALERT_CONTEXT})` : '';
const lines = failures.map((f) => `• *${f.name}*: ${f.reason}`).join('\n');
await postSlack(
  `:warning: LeadDataScraper external dependency degraded${ctx} — ${failures.length}/${results.length} checks failed.\n${lines}\n_Backend code may be fine; do NOT roll back on this alone._`,
);

console.error(`\nDEPS_DEGRADED count=${failures.length}`);
process.exit(1);
