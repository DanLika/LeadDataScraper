#!/usr/bin/env node
// Endpoint contract smoke. Loads every JSON Schema in tests/contracts/
// and pings the matching endpoint, asserting status code, Content-Type,
// and response-shape match the contract.
//
// Contract file shape (tests/contracts/<name>.json):
// {
//   "name": "leads_get",
//   "method": "GET",
//   "path": "/leads",
//   "query": "?limit=1",                    // optional
//   "body": { ... },                        // optional, for POST/PUT
//   "expected_status": 200,
//   "expected_content_type": "application/json",
//   "auth": "api_key" | "admin" | "none",
//   "schema": { ...JSON Schema for response body }
// }
//
// Required env:
//   BACKEND_URL              backend base (https://...)
//   API_SECRET_KEY           for `auth: api_key` contracts
// Optional:
//   ADMIN_TOKEN              for `auth: admin` contracts
//   CONTRACTS_DIR            default ./tests/contracts
//   SLACK_WEBHOOK_URL

import { readdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const {
  BACKEND_URL,
  API_SECRET_KEY,
  ADMIN_TOKEN,
  CONTRACTS_DIR,
  SLACK_WEBHOOK_URL,
} = process.env;

if (!BACKEND_URL || !API_SECRET_KEY) {
  console.error('MISSING_ENV BACKEND_URL or API_SECRET_KEY');
  process.exit(2);
}

let Ajv;
try {
  Ajv = (await import('ajv')).default;
} catch {
  console.error('MISSING_DEP ajv — install with `npm install --no-save ajv@8` before running.');
  process.exit(2);
}
// allErrors:false — Semgrep CWE-400 hardening. We only surface the first
// validation error anyway (the `slice(0, 3)` below was overprovisioned),
// and an attacker-controlled response shape would otherwise let a hostile
// upstream emit unlimited error objects.
const ajv = new Ajv({ allErrors: false, strict: false });

const dir = resolve(CONTRACTS_DIR || './tests/contracts');
const failures = [];

function authHeaders(authMode) {
  const h = { 'Content-Type': 'application/json' };
  if (authMode === 'api_key') h['X-API-Key'] = API_SECRET_KEY;
  if (authMode === 'admin') {
    h['X-API-Key'] = API_SECRET_KEY;
    if (ADMIN_TOKEN) h['X-Admin-Token'] = ADMIN_TOKEN;
  }
  return h;
}

async function runContract(file, contract) {
  const label = `${contract.method} ${contract.path}`;
  const url = `${BACKEND_URL.replace(/\/$/, '')}${contract.path}${contract.query || ''}`;
  const init = {
    method: contract.method,
    headers: authHeaders(contract.auth || 'api_key'),
  };
  if (contract.body !== undefined && contract.method !== 'GET' && contract.method !== 'HEAD') {
    init.body = JSON.stringify(contract.body);
  }

  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    failures.push({ file, label, reason: `fetch error: ${e.message}` });
    console.log(`[FAIL] ${label} — fetch error: ${e.message}`);
    return;
  }

  if (res.status !== contract.expected_status) {
    failures.push({ file, label, reason: `status=${res.status} expected=${contract.expected_status}` });
    console.log(`[FAIL] ${label} — status=${res.status} expected=${contract.expected_status}`);
    return;
  }

  const ct = (res.headers.get('content-type') || '').toLowerCase();
  const expectedCt = (contract.expected_content_type || 'application/json').toLowerCase();
  if (!ct.includes(expectedCt)) {
    failures.push({ file, label, reason: `content-type=${ct} expected~${expectedCt}` });
    console.log(`[FAIL] ${label} — content-type=${ct} expected~${expectedCt}`);
    return;
  }

  if (ct.includes('json') && contract.schema) {
    let body;
    try {
      body = await res.json();
    } catch (e) {
      failures.push({ file, label, reason: `body not JSON: ${e.message}` });
      console.log(`[FAIL] ${label} — body not JSON: ${e.message}`);
      return;
    }
    const validate = ajv.compile(contract.schema);
    const ok = validate(body);
    if (!ok) {
      const errors = (validate.errors || []).slice(0, 3).map((e) => `${e.instancePath} ${e.message}`).join('; ');
      failures.push({ file, label, reason: `schema mismatch: ${errors}` });
      console.log(`[FAIL] ${label} — schema mismatch: ${errors}`);
      return;
    }
  }

  console.log(`[PASS] ${label}`);
}

let entries;
try {
  entries = (await readdir(dir)).filter((f) => f.endsWith('.json'));
} catch (e) {
  console.error(`CONTRACTS_DIR not found: ${dir} (${e.message})`);
  process.exit(2);
}
if (entries.length === 0) {
  console.error(`No contracts in ${dir} — add at least one JSON file.`);
  process.exit(2);
}

for (const file of entries) {
  let contract;
  try {
    contract = JSON.parse(await readFile(join(dir, file), 'utf8'));
  } catch (e) {
    failures.push({ file, label: file, reason: `invalid JSON: ${e.message}` });
    continue;
  }
  await runContract(file, contract);
}

if (failures.length > 0) {
  if (SLACK_WEBHOOK_URL) {
    try {
      await fetch(SLACK_WEBHOOK_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text:
            `:warning: LeadDataScraper contract smoke failed — ${failures.length}/${entries.length}.\n` +
            failures.slice(0, 8).map((f) => `• *${f.label}*: ${f.reason}`).join('\n'),
        }),
      });
    } catch { /* best-effort */ }
  }
  console.error(`\nCONTRACT_SMOKE_FAILED count=${failures.length}/${entries.length}`);
  process.exit(1);
}
console.log(`\nCONTRACT_SMOKE_PASSED ${entries.length}/${entries.length}`);
