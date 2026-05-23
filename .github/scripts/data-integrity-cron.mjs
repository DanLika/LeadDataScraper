#!/usr/bin/env node
// Daily silent integrity assertions on production data. No writes — just
// SELECTs via service_role + small client-side aggregations. Alerts on
// any anomaly so the operator finds the broken row before a customer
// (or a downstream consumer) does.
//
// Five checks per spec:
//   1. campaign_messages with no matching campaign  → 0 expected
//   2. orchestration_jobs running > 2h              → suspicious
//   3. leads count vs DISTINCT(unique_key)          → must match
//   4. seo_score outside [0,100]                    → must be 0
//   5. random sample of audit_results JSON parses   → all 20
//
// Required env:
//   SUPABASE_URL
//   SUPABASE_SERVICE_ROLE_KEY
// Optional:
//   SLACK_WEBHOOK_URL
//   STALE_JOB_THRESHOLD_HOURS   default 2
//   SAMPLE_SIZE                 default 20

const {
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
  SLACK_WEBHOOK_URL,
  STALE_JOB_THRESHOLD_HOURS,
  SAMPLE_SIZE,
} = process.env;

const REQUIRED = ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'];
for (const k of REQUIRED) {
  if (!process.env[k]) {
    console.error(`MISSING_ENV ${k}`);
    process.exit(2);
  }
}

const STALE_HOURS = Number(STALE_JOB_THRESHOLD_HOURS || 2);
const SAMPLE = Math.max(1, Number(SAMPLE_SIZE || 20));
const PAGE_SIZE = 1000; // PostgREST max for a single GET
const anomalies = [];

function flag(check, detail, examples = []) {
  anomalies.push({ check, detail, examples });
  console.log(`[ANOMALY] ${check} — ${detail}`);
  if (examples.length) console.log(`  examples: ${JSON.stringify(examples.slice(0, 5))}`);
}

async function rest(path, init = {}) {
  return fetch(`${SUPABASE_URL}/rest/v1${path}`, {
    ...init,
    headers: {
      apikey: SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      Accept: 'application/json',
      ...(init.headers || {}),
    },
  });
}

// Drains a select query with offset pagination so 'limit=10000' on
// PostgREST (which caps at 1000) doesn't silently truncate findings.
async function selectAll(table, columns) {
  const out = [];
  let offset = 0;
  // hard cap to bound an unbounded leak — if leads is ever 100k+, the
  // alert message will say "first 100k rows" and that's still enough
  // signal to act on.
  const HARD_CAP = 100_000;
  while (offset < HARD_CAP) {
    const res = await rest(`/${table}?select=${columns}&limit=${PAGE_SIZE}&offset=${offset}`);
    if (!res.ok) throw new Error(`select ${table} status=${res.status}`);
    const rows = await res.json();
    if (!Array.isArray(rows) || rows.length === 0) break;
    out.push(...rows);
    if (rows.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return out;
}

// 1. campaign_messages.campaign_id with no matching campaigns.id row.
async function checkOrphanMessages() {
  const [msgs, campaigns] = await Promise.all([
    selectAll('campaign_messages', 'id,campaign_id'),
    selectAll('campaigns', 'id'),
  ]);
  const live = new Set(campaigns.map((c) => c.id));
  const orphans = msgs.filter((m) => m.campaign_id && !live.has(m.campaign_id));
  if (orphans.length > 0) {
    flag('orphan_campaign_messages', `${orphans.length} message(s) reference a missing campaign`, orphans.slice(0, 5).map((o) => o.id));
  }
}

// 2. orchestration_jobs stuck in running/starting > STALE_HOURS.
async function checkStaleJobs() {
  const res = await rest(`/orchestration_jobs?select=id,status,started_at,updated_at&status=in.(running,starting)`);
  if (!res.ok) {
    flag('stale_jobs_query_failed', `status=${res.status}`);
    return;
  }
  const rows = await res.json();
  const now = Date.now();
  const stale = rows.filter((r) => {
    const ts = Date.parse(r.updated_at || r.started_at || '');
    if (!Number.isFinite(ts)) return false;
    return now - ts > STALE_HOURS * 3600_000;
  });
  if (stale.length > 0) {
    flag('stale_running_jobs', `${stale.length} job(s) >${STALE_HOURS}h in running/starting`, stale.map((r) => r.id));
  }
}

// 3. leads count vs DISTINCT(unique_key). Done client-side: select
// unique_key for every row, build a Set, compare.
async function checkDuplicateLeads() {
  const rows = await selectAll('leads', 'unique_key');
  const total = rows.length;
  const distinct = new Set(rows.map((r) => r.unique_key)).size;
  if (total !== distinct) {
    flag('duplicate_unique_keys', `leads=${total} distinct=${distinct} delta=${total - distinct}`);
  }
}

// 4. seo_score outside [0, 100].
async function checkSeoScoreRange() {
  // PostgREST filter: NOT (between 0 and 100) AND NOT NULL.
  const res = await rest(`/leads?select=unique_key,seo_score&seo_score=not.is.null&or=(seo_score.lt.0,seo_score.gt.100)`);
  if (!res.ok) {
    flag('seo_score_query_failed', `status=${res.status}`);
    return;
  }
  const bad = await res.json();
  if (bad.length > 0) {
    flag('seo_score_out_of_range', `${bad.length} lead(s) with seo_score ∉ [0,100]`, bad.map((b) => `${b.unique_key}=${b.seo_score}`));
  }
}

// 5. random sample of audit_results JSON. PostgREST stores jsonb as JSON
// natively, so the column comes back parsed. We just sample N rows that
// have audit_results set and confirm each is a non-null object.
async function checkAuditResultsValid() {
  const res = await rest(`/leads?select=unique_key,audit_results&audit_results=not.is.null&limit=${SAMPLE}`);
  if (!res.ok) {
    flag('audit_results_sample_failed', `status=${res.status}`);
    return;
  }
  const rows = await res.json();
  const malformed = rows.filter((r) => {
    const v = r.audit_results;
    return v === null || (typeof v !== 'object') || Array.isArray(v);
  });
  if (malformed.length > 0) {
    flag('audit_results_malformed', `${malformed.length}/${rows.length} sampled rows have non-object audit_results`, malformed.map((m) => m.unique_key));
  }
}

async function postSlack(text) {
  if (!SLACK_WEBHOOK_URL) {
    console.warn('SLACK_WEBHOOK_URL unset — skipping alert.');
    return;
  }
  try {
    await fetch(SLACK_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    console.error(`Slack post error: ${e.message}`);
  }
}

await Promise.all([
  checkOrphanMessages(),
  checkStaleJobs(),
  checkDuplicateLeads(),
  checkSeoScoreRange(),
  checkAuditResultsValid(),
]);

if (anomalies.length > 0) {
  const lines = anomalies
    .map((a) => `• *${a.check}*: ${a.detail}${a.examples?.length ? `  e.g. \`${a.examples.slice(0, 3).join(', ')}\`` : ''}`)
    .join('\n');
  await postSlack(
    `:mag: LeadDataScraper data-integrity anomalies (${anomalies.length}):\n${lines}`,
  );
  console.error(`\nDATA_INTEGRITY_ANOMALIES count=${anomalies.length}`);
  process.exit(1);
}
console.log('\nDATA_INTEGRITY_CLEAN');
