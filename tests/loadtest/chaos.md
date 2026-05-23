# Chaos / recovery test runbook

Three scenarios. Each follows the same shape:

1. Start steady-state load.
2. Inject the failure.
3. Measure recovery (first successful response after restoration).
4. Verify data integrity post-recovery.

Failures are intentionally narrow — single-fault-injection. Don't combine them on the first pass; you won't be able to attribute the bad signal.

---

## Scenario A: backend dyno restart mid-load

Simulates: deployment swap, OOM-kill, manual restart, scheduled health bounce.

### Pre-flight

- `LOAD_API_BASE` + `LOAD_API_KEY` exported
- Render API token in `RENDER_API_TOKEN` (Account Settings → API Keys)
- Render service ID in `RENDER_SERVICE_ID` (URL slug or `gh repo view` parity)
- Test database (not prod) — Scenario A causes at most a few seconds of write loss; that's acceptable on a test DB but not on real data

### Procedure

```bash
# T=0  Start load
locust -f tests/loadtest/locustfile.py --headless \
  --tags read --users 50 --spawn-rate 10 --run-time 5m \
  --host "$LOAD_API_BASE" \
  --html tests/loadtest/reports/chaos_A.html \
  --csv  tests/loadtest/reports/chaos_A &
LOCUST_PID=$!

# T=60  Restart Render
sleep 60
curl -s -X POST \
  "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
  -H "Authorization: Bearer $RENDER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"clearCache":"do_not_clear"}'

# Locust keeps running. The wrapper continues for the full 5m so the
# recovery window is recorded.
wait $LOCUST_PID
```

### Measure

- **Outage duration**: scan `chaos_A_failures.csv` for the first failure timestamp after T=60s and the last failure timestamp. Diff = outage seconds.
- **First successful response after restart**: tail `chaos_A_stats_history.csv` from T=60s onward; find the first row with `total_rps > 0` and 0 failures in the previous interval.
- **% requests that failed**: from the aggregated summary row — divide failure count by total. Acceptable: < 1% over the 5-min window (the restart blackhole is the dominant cost).
- **DB state integrity**: post-run, run `SELECT COUNT(*) FROM orchestration_jobs WHERE status = 'running' AND updated_at < NOW() - INTERVAL '15 minutes'` on Supabase. Any rows = `recover_interrupted_jobs` should mop them up on the *next* dyno restart. Verify by checking the lifespan log.

### Pass criteria

- Outage < 30 s end-to-end (Render free tier typical: 15-25 s).
- Locust's first successful response after restart arrives within 10 s of the deploy completion ping.
- No `running` orchestration_jobs rows older than 10 minutes 15 minutes after the run finishes.

---

## Scenario B: Supabase pool drop (transient connection failure)

Simulates: brief Supabase outage, postgres-side restart, network blip in the eu-west-1 ↔ Render path.

### Two ways to inject

**B.1 — via `drop_supabase_pool.py` (preferred, no infra access needed):**
Monkey-patches the in-process supabase client's httpx pool to refuse new connections for 30 s. Local-only — runs against your laptop instance. Use this for quick CI-style verification.

```bash
LOAD_API_BASE=http://127.0.0.1:8000 LOAD_API_KEY=… ./tests/loadtest/drop_supabase_pool.py --hold 30
# In parallel: locust against /leads + /stats
```

**B.2 — via Supabase dashboard:**
Pause the connection pooler (Project Settings → Database → Pooler) for 30-60 s. More realistic but only doable against your own test project, not prod.

### Measure

- /leads + /stats error spike duration matches the injection window.
- Backend logs show APIError lines but no 5xx escapes to the client (the existing `except APIError` blocks return 502 with a generic message).
- After restoration, the first successful /leads response arrives within 5 s (httpx reconnects on first use, no app-side warm-up needed).
- No orphaned `orchestration_jobs` left in `starting`/`running` — the in-flight job either completes or `recover_interrupted_jobs` reclaims it.

### Pass criteria

- Operator-visible behaviour during outage: 502 with the generic body. **No 500-with-stack-trace leak.**
- Recovery time: backend serves /leads OK within 5 s of pool restoration.
- No `Pool exhausted` log lines — the existing async wrappers should yield the loop while waiting, not pile up.

---

## Scenario C: Gemini API timeout

Simulates: Google Cloud regional incident, your account hitting a per-minute quota, malformed prompt landing in a slow path.

### Inject

Mock Gemini latency with a local HTTP intercept. The simplest path: temporarily point `GEMINI_API_KEY` at a local HTTP-toxiproxy or set an env shim. The chaos script `drop_supabase_pool.py` is designed to be extended — copy its pattern with a 30 s `asyncio.sleep` wrapper around `genai.Client.generate_content`.

A lighter approach for "what happens when /ask is slow": call /ask in a tight loop while disabling the backend's outbound network briefly (e.g. via `iptables -A OUTPUT -d generativelanguage.googleapis.com -j DROP` on the host).

### Measure

- /ask handler timing — does it return within its own timeout (defined where? grep `genai_types.GenerateContentConfig`)?
- If no timeout in the handler: every Gemini-slowdown blocks the entire /ask path. Action: add explicit `asyncio.wait_for(timeout=20)` around the generate_content call.
- /audit-status, /stats, /leads — these should be **unaffected**. If they slow down too, there's a hidden cross-dependency in the AI router import or shared event loop.

### Pass criteria

- /ask returns 504 (or generic 503) within 20 s, NOT hangs indefinitely.
- /leads + /stats + /audit-status unaffected by Gemini outage.
- After Gemini restoration, next /ask succeeds without warm-up.

---

## What to write down for each run

Save under `tests/loadtest/reports/chaos_<scenario>_<YYYYMMDD>.md`:

| Field | Example |
|-------|---------|
| Scenario | A — Render restart |
| Start | `2026-05-22T14:00:00Z` |
| End | `2026-05-22T14:05:00Z` |
| Injection time | T+60s |
| Outage start (first 5xx) | T+62.4s |
| Outage end (first 200) | T+78.1s |
| Outage duration | 15.7 s |
| % requests failed | 0.6 % |
| DB integrity post-run | clean (0 stale jobs) |
| Logs grep `slow handler:` count | 0 |
| Verdict | PASS |
| Notes | Render deploy-swap took 14 s on this run, matches recent baseline. |

---

## Anti-goals

- Don't run chaos against the **prod** Supabase. Use a snapshot or a dedicated test project. Restart-induced write loss is mostly benign on a real-customer DB but Scenario B's `drop pool` can lose in-flight rows.
- Don't combine scenarios on the first cycle. Once each passes individually, you can stack them; combined faults teach you about ordering / partial-recovery behaviour but introduce too many variables for a first signal.
- Don't tune `_rate_limit_key` or any limiter during a chaos run. Chaos asserts *current* behaviour; tuning during the run invalidates the result.
