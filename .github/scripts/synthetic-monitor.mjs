#!/usr/bin/env node
// Synthetic monitor — runs every 5 min via cron, hits 4 lightweight
// endpoints, persists rolling history + streak counter to a private
// gist, and pages Slack on the 3rd consecutive failure (and on
// recovery). Designed to do nothing surprising on transient flakes —
// the 3-strike rule is the only thing that alerts.

const {
  BACKEND_URL,
  FRONTEND_URL,
  API_SECRET_KEY,
  GIST_TOKEN,
  MONITOR_GIST_ID,
  SLACK_WEBHOOK_URL,
  DISCORD_WEBHOOK_URL,
} = process.env;

const REQUIRED = ['BACKEND_URL', 'FRONTEND_URL', 'API_SECRET_KEY', 'GIST_TOKEN', 'MONITOR_GIST_ID'];
for (const k of REQUIRED) {
  if (!process.env[k]) {
    console.error(`MISSING_ENV ${k}`);
    process.exit(2);
  }
}
if (!DISCORD_WEBHOOK_URL && !SLACK_WEBHOOK_URL) {
  // Not a hard failure — operator may have intentionally muted alerts.
  // Streak detection + gist history continue regardless. The alert
  // call sites log a warning when neither URL is set.
  console.warn('NEITHER DISCORD_WEBHOOK_URL NOR SLACK_WEBHOOK_URL set — alerts will be silent.');
}

const GIST_FILENAME = 'monitor.json';
const HISTORY_CAP = 100;
const ALERT_THRESHOLD = 3;
const TIMEOUT_MS = 10_000;

async function withTimeout(promise, ms = TIMEOUT_MS) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    return await promise(ctl.signal);
  } finally {
    clearTimeout(t);
  }
}

async function checkBackendRoot() {
  return withTimeout(async (signal) => {
    const res = await fetch(`${BACKEND_URL}/`, { signal });
    if (res.status !== 200) return { ok: false, reason: `root status=${res.status}` };
    const body = await res.json().catch(() => null);
    if (body?.status !== 'ok') return { ok: false, reason: `root body=${JSON.stringify(body)}` };
    return { ok: true };
  });
}

async function checkSchema() {
  return withTimeout(async (signal) => {
    const res = await fetch(`${BACKEND_URL}/health/schema`, {
      headers: { 'X-API-Key': API_SECRET_KEY },
      signal,
    });
    if (res.status !== 200) return { ok: false, reason: `schema status=${res.status}` };
    const body = await res.json();
    if (body.drift !== false) {
      return { ok: false, reason: `schema drift=${body.drift} missing=${body.missing_columns_count}` };
    }
    return { ok: true };
  });
}

async function checkStats() {
  return withTimeout(async (signal) => {
    const res = await fetch(`${BACKEND_URL}/stats`, {
      headers: { 'X-API-Key': API_SECRET_KEY },
      signal,
    });
    if (res.status !== 200) return { ok: false, reason: `stats status=${res.status}` };
    const body = await res.json().catch(() => null);
    if (body == null || typeof body.total_leads !== 'number') {
      return { ok: false, reason: `stats body shape unexpected` };
    }
    return { ok: true };
  });
}

async function checkFrontend() {
  return withTimeout(async (signal) => {
    // HEAD `/login` — /login is the unauth landing, no redirect chain,
    // no body cost. Accept any 2xx/3xx as "frontend reachable".
    const res = await fetch(`${FRONTEND_URL}/login`, { method: 'HEAD', redirect: 'manual', signal });
    if (res.status < 200 || res.status >= 400) {
      return { ok: false, reason: `frontend status=${res.status}` };
    }
    return { ok: true };
  });
}

async function runChecks() {
  const checks = {};
  const failReasons = [];

  for (const [name, fn] of [
    ['backend_root', checkBackendRoot],
    ['schema', checkSchema],
    ['stats', checkStats],
    ['frontend', checkFrontend],
  ]) {
    try {
      const r = await fn();
      checks[name] = r.ok ? 'ok' : 'fail';
      if (!r.ok) failReasons.push(`${name}: ${r.reason}`);
    } catch (e) {
      checks[name] = 'fail';
      failReasons.push(`${name}: error=${e.name === 'AbortError' ? 'timeout' : e.message}`);
    }
  }

  const ok = failReasons.length === 0;
  return {
    ts: new Date().toISOString(),
    ok,
    checks,
    fail_reasons: failReasons,
  };
}

async function loadGist() {
  const res = await fetch(`https://api.github.com/gists/${MONITOR_GIST_ID}`, {
    headers: {
      'Authorization': `Bearer ${GIST_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'synthetic-monitor',
    },
  });
  if (!res.ok) {
    throw new Error(`gist load failed status=${res.status} body=${await res.text()}`);
  }
  const data = await res.json();
  const file = data.files?.[GIST_FILENAME];
  if (!file) {
    // First run — gist exists but file missing. Seed default shape.
    return { consecutive_failures: 0, alerted_at_streak: 0, results: [] };
  }
  try {
    const parsed = JSON.parse(file.content);
    return {
      consecutive_failures: Number.isFinite(parsed.consecutive_failures) ? parsed.consecutive_failures : 0,
      alerted_at_streak: Number.isFinite(parsed.alerted_at_streak) ? parsed.alerted_at_streak : 0,
      results: Array.isArray(parsed.results) ? parsed.results : [],
    };
  } catch {
    // Corrupted JSON — log and re-seed rather than crash the monitor.
    console.warn('Gist JSON corrupted, re-seeding.');
    return { consecutive_failures: 0, alerted_at_streak: 0, results: [] };
  }
}

async function saveGist(state) {
  const res = await fetch(`https://api.github.com/gists/${MONITOR_GIST_ID}`, {
    method: 'PATCH',
    headers: {
      'Authorization': `Bearer ${GIST_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'User-Agent': 'synthetic-monitor',
    },
    body: JSON.stringify({
      files: { [GIST_FILENAME]: { content: JSON.stringify(state, null, 2) } },
    }),
  });
  if (!res.ok) {
    throw new Error(`gist save failed status=${res.status} body=${await res.text()}`);
  }
}

// Discord embed colours (decimal) matching the composite action's
// .github/actions/discord-notify scheme — operator sees the same colour
// language across every alert source.
const DISCORD_COLOURS = {
  critical: 15158332,  // red
  error:    15158332,
  warning:  15844367,  // amber
  info:     3447003,   // blue
};

async function postDiscord(severity, title, description) {
  if (!DISCORD_WEBHOOK_URL) return false;
  const color = DISCORD_COLOURS[severity] ?? 9807270;
  const payload = {
    embeds: [{
      title: title.slice(0, 256),
      description: description.slice(0, 4000),
      color,
      timestamp: new Date().toISOString(),
    }],
  };
  const res = await fetch(DISCORD_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    console.error(`Discord post failed status=${res.status} body=${await res.text()}`);
    return false;
  }
  return true;
}

async function postSlack(text, blocks) {
  if (!SLACK_WEBHOOK_URL) {
    console.warn('SLACK_WEBHOOK_URL unset — skipping Slack alert.');
    return false;
  }
  const res = await fetch(SLACK_WEBHOOK_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, blocks }),
  });
  if (!res.ok) {
    console.error(`Slack post failed status=${res.status} body=${await res.text()}`);
    return false;
  }
  return true;
}

// Unified alert sink. Prefers Discord-native embed (richer formatting,
// matches the composite action used by every other workflow); falls
// back to Slack-shaped POST when only SLACK_WEBHOOK_URL is set. If
// neither is set, logs a warning and returns — no exception, so a
// muted-alerts run still progresses through gist history + streak
// bookkeeping.
async function postAlert(severity, title, description) {
  if (await postDiscord(severity, title, description)) return;
  // Slack fallback gets a single-text payload; the title prefix mirrors
  // the Discord embed title so the operator's grep / channel-search
  // history is consistent across channels.
  const text = `${title}\n${description}`;
  if (await postSlack(text)) return;
  console.warn('Both DISCORD_WEBHOOK_URL and SLACK_WEBHOOK_URL are unset — alert dropped.');
}

function uptimePct(results, hours) {
  const cutoff = Date.now() - hours * 3600_000;
  const recent = results.filter((r) => new Date(r.ts).getTime() >= cutoff);
  if (recent.length === 0) return null;
  const passed = recent.filter((r) => r.ok).length;
  return ((passed / recent.length) * 100).toFixed(2);
}

const result = await runChecks();
console.log(JSON.stringify(result));

const state = await loadGist();
state.results.push(result);
if (state.results.length > HISTORY_CAP) {
  state.results = state.results.slice(-HISTORY_CAP);
}

if (result.ok) {
  const wasAlerting = state.alerted_at_streak > 0;
  state.consecutive_failures = 0;
  if (wasAlerting) {
    const up24 = uptimePct(state.results, 24);
    await postAlert(
      'info',
      '✅ Synthetic monitor: recovered',
      `All 4 checks passing again. 24h uptime: ${up24 ?? 'n/a'}%`,
    );
    state.alerted_at_streak = 0;
  }
} else {
  state.consecutive_failures += 1;
  const streak = state.consecutive_failures;
  const shouldAlert = streak >= ALERT_THRESHOLD && state.alerted_at_streak === 0;
  if (shouldAlert) {
    const up24 = uptimePct(state.results, 24);
    const lines = result.fail_reasons.map((r) => `• ${r}`).join('\n');
    await postAlert(
      'error',
      `🚨 Synthetic monitor: ${streak} consecutive failures`,
      `${lines}\n24h uptime: ${up24 ?? 'n/a'}%`,
    );
    state.alerted_at_streak = streak;
  }
}

await saveGist(state);

// Exit 0 so the cron workflow doesn't show a red X on every single failed
// check — Slack alert + gist history are the user-facing signal. Exit
// non-zero only on infrastructure errors (handled by throws above).
process.exit(0);
