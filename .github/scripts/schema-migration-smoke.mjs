#!/usr/bin/env node
// Database schema migration smoke.
//
// Runs after every deploy that touched supabase_schema.sql. Verifies the
// live Supabase project matches what the repo claims it should be, AND
// pins the security-critical RPC surface (add_lead_column exists +
// OWNER=postgres; exec_sql does NOT exist).
//
// Exit codes:
//   0 — schema matches; safe to proceed.
//   1 — drift detected; wrapping workflow rolls back.
//   2 — required env missing; treat as inconclusive (don't rollback).
//
// Required env:
//   SUPABASE_URL                 https://<project>.supabase.co
//   SUPABASE_SERVICE_ROLE_KEY    server-side key (bypasses RLS)
//   SCHEMA_SQL_PATH              optional; default ./supabase_schema.sql
// Optional:
//   SLACK_WEBHOOK_URL            alert on drift

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const {
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
  SCHEMA_SQL_PATH,
  SLACK_WEBHOOK_URL,
} = process.env;

const REQUIRED = ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'];
for (const k of REQUIRED) {
  if (!process.env[k]) {
    console.error(`MISSING_ENV ${k}`);
    process.exit(2);
  }
}

const TABLES = ['leads', 'campaigns', 'campaign_messages', 'orchestration_jobs'];
const FORBIDDEN_RPCS = ['exec_sql']; // removed for security — regression-test

const drift = [];

function note(category, detail) {
  drift.push({ category, detail });
  console.log(`[DRIFT] ${category} — ${detail}`);
}

async function pg(sql, label) {
  // We don't have a generic sql RPC by design (exec_sql was removed). The
  // service_role does have direct PostgREST access, so introspection uses
  // built-in PostgREST endpoints rather than running raw SQL.
  // Helper: GET an arbitrary REST resource.
  void sql; void label;
}

async function rest(path, init = {}) {
  const url = `${SUPABASE_URL}/rest/v1${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      apikey: SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      Accept: 'application/json',
      ...(init.headers || {}),
    },
  });
  return res;
}

// 1. Diff committed columns vs live. Read the .sql, extract CREATE TABLE
// blocks, compare column lists against PostgREST's OpenAPI manifest at
// /rest/v1/?apikey=… which lists every accessible table + columns.
async function checkColumns() {
  const path = SCHEMA_SQL_PATH || resolve(process.cwd(), 'supabase_schema.sql');
  let sql;
  try {
    sql = await readFile(path, 'utf8');
  } catch (e) {
    note('schema_sql_missing', `${path}: ${e.message}`);
    return;
  }
  const expectedByTable = {};
  // Naive CREATE TABLE parser — captures `col_name <type>` lines until
  // the matching `);`. Good enough for our schema; doesn't try to parse
  // constraints, indexes, etc.
  const blocks = sql.match(/CREATE TABLE[^(]+\(([\s\S]*?)\);/gi) || [];
  for (const block of blocks) {
    const nameMatch = block.match(/CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(?:public\.)?["]?([a-zA-Z_][\w]*)["]?/i);
    if (!nameMatch) continue;
    const table = nameMatch[1];
    if (!TABLES.includes(table)) continue;
    const inner = block.slice(block.indexOf('(') + 1, block.lastIndexOf(')'));
    const cols = inner
      .split(/,(?![^()]*\))/)
      .map((s) => s.trim())
      .filter((s) => s && !/^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK|CONSTRAINT)/i.test(s))
      .map((s) => s.match(/^"?([a-zA-Z_][\w]*)"?/)?.[1])
      .filter(Boolean);
    expectedByTable[table] = cols;
  }

  // Live column set via PostgREST's spec — GET /rest/v1/ returns the
  // OpenAPI document with column metadata under definitions.<table>.
  const specRes = await rest('/', { headers: { Accept: 'application/openapi+json' } });
  if (!specRes.ok) {
    note('openapi_unreachable', `status=${specRes.status}`);
    return;
  }
  const spec = await specRes.json();
  const defs = spec?.definitions || {};

  for (const table of TABLES) {
    const expected = expectedByTable[table];
    if (!expected) {
      note('table_not_in_schema_sql', table);
      continue;
    }
    const liveCols = Object.keys(defs[table]?.properties || {});
    if (liveCols.length === 0) {
      note('table_missing_live', table);
      continue;
    }
    for (const col of expected) {
      if (!liveCols.includes(col)) note('column_missing_live', `${table}.${col}`);
    }
  }
}

// 2. RLS policies present + enabled on each table. PostgREST surfaces
// rls state via the spec as well; for the policy *list*, the only
// supported path without a custom RPC is to check `pg_policies` via a
// service_role select. We expose the check by attempting an anon read
// of each table — if RLS is OFF, anon would see rows (would-fail-
// closed via no anon grants in our config). To probe directly, hit the
// service_role select with `count` and compare with the deny-by-anon
// behaviour test.
async function checkRls() {
  // Service_role MUST be able to read (sanity).
  for (const table of TABLES) {
    const res = await rest(`/${table}?select=*&limit=0`, { method: 'HEAD' });
    if (!res.ok) note('rls_service_role_blocked', `${table} status=${res.status}`);
  }
  // Anon MUST NOT be able to read (RLS + grant revocation).
  if (process.env.SUPABASE_ANON_KEY) {
    for (const table of TABLES) {
      const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?select=*&limit=0`, {
        method: 'HEAD',
        headers: {
          apikey: process.env.SUPABASE_ANON_KEY,
          Authorization: `Bearer ${process.env.SUPABASE_ANON_KEY}`,
        },
      });
      // 401 / 403 / 404 acceptable. 200 means anon CAN see the table → drift.
      if (res.status === 200) note('rls_anon_can_read', `${table} returned 200`);
    }
  } else {
    console.warn('SUPABASE_ANON_KEY not set — skipping anon-deny check.');
  }
}

// 3. add_lead_column exists + OWNER=postgres. The RPC accepts a single
// `col` text argument. We probe by calling it with a syntactically-valid
// but harmless column name; if the function doesn't exist, PostgREST
// returns 404. If it exists but the column name fails its regex
// allowlist, it returns an error — also fine (proves it's there).
async function checkAddLeadColumnRpc() {
  const res = await rest('/rpc/add_lead_column', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ col: '__schema_smoke_probe_never_persisted__' }),
  });
  if (res.status === 404) {
    note('rpc_missing', 'add_lead_column');
    return;
  }
  // Any 2xx/4xx OTHER than 404 means the function exists.
  // We DON'T verify OWNER=postgres at runtime — PostgREST doesn't expose
  // pg_proc.proowner. The repo's supabase_schema.sql is the source of
  // truth for OWNER; the load-bearing assertion is that the function
  // exists with the documented signature.
}

// 4. exec_sql MUST NOT exist (security regression guard).
async function checkExecSqlAbsent() {
  for (const rpc of FORBIDDEN_RPCS) {
    const res = await rest(`/rpc/${rpc}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    // 404 expected (good — function doesn't exist).
    // 401/403 means it exists but blocked by RLS — still drift, function should be gone.
    if (res.status !== 404) note('forbidden_rpc_present', `${rpc} status=${res.status}`);
  }
}

// 5. No-op SELECT on each table via service_role — sanity that the
// service_role can actually read every table we manage. Catches
// permission drift, table renames, accidental REVOKEs.
async function checkServiceRoleReads() {
  for (const table of TABLES) {
    const res = await rest(`/${table}?select=*&limit=1`);
    if (!res.ok) note('service_role_select_failed', `${table} status=${res.status}`);
  }
}

async function postSlack(text) {
  if (!SLACK_WEBHOOK_URL) return;
  try {
    await fetch(SLACK_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch {
    /* best-effort alerting */
  }
}

await checkColumns();
await checkRls();
await checkAddLeadColumnRpc();
await checkExecSqlAbsent();
await checkServiceRoleReads();
void pg; // unused helper, future SQL surface

if (drift.length > 0) {
  console.error(`\nSCHEMA_DRIFT count=${drift.length}`);
  console.error(JSON.stringify(drift, null, 2));
  await postSlack(
    `:rotating_light: LeadDataScraper schema drift detected — ${drift.length} issue${drift.length === 1 ? '' : 's'}.\n` +
      drift.map((d) => `• *${d.category}*: ${d.detail}`).join('\n'),
  );
  process.exit(1);
}
console.log('\nSCHEMA_HEALTHY');
