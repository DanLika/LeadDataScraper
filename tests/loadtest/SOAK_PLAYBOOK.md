# Soak test playbook

What to watch during a 24h `soak.sh` run, what each signal means, and the action threshold for each.

## Pre-flight (T-0)

- [ ] `LOAD_API_BASE` and `LOAD_API_KEY` exported.
- [ ] Backend is on the green-deploy commit, no in-flight Render restarts.
- [ ] Supabase project shows ACTIVE_HEALTHY (Lead Scraper project).
- [ ] Note baseline RSS (Render dashboard or `ps -o rss -p $(pgrep -f uvicorn)` if local).
- [ ] Note baseline file-descriptor count (local: `lsof -p $(pgrep -f uvicorn) | wc -l`).
- [ ] Kick off: `./soak.sh 24h`.

## Active monitoring (every 2-4h, or on alert)

| Signal | Tool | Healthy | Investigate |
|--------|------|---------|-------------|
| Backend RSS | Render dashboard "Memory" graph | Flat or sawtooth ±10% around baseline | Monotonic growth → leak. Capture heap snapshot before next restart. |
| File descriptors | `lsof \| grep python \| wc -l` | Steady; bumps only during orchestrator jobs and drop back | Sustained growth = playwright browser not closed or `aiohttp.ClientSession` not exiting. Cross-check enrichment_engine.aclose() in finally blocks. |
| DB connection count | Supabase dashboard → Database → "Pool Mode" tab | ≤ 50% of pool size | Climbing toward limit = sync `.execute()` sneaking into async path. Re-grep for the pattern Locust 4.1 flagged. |
| Render dyno restarts | Render Events tab | 0 | Each restart = silent crash. Pull last log lines around restart timestamp. |
| /leads p95 | `reports/soak_*/leads_stats.csv` | Within 2× of cold-cache baseline | Growing p95 = pagination index lag or pandas-on-stats regression. |
| /stats hit rate | Backend logs grep `slow handler: GET /stats` | Most calls < 50ms (cache hit) | Frequent slow ones = cache invalidation storm. Check stats_cache.invalidate() callers. |
| Orchestrator success | `reports/soak_*/orchestrator_*.json` | 200 every hour, job_id present | 503 = DB pool exhausted. 500 = uncaught exception (check logs). |
| Backend log volume | Render log retention or local file | Steady KB/hr | Spike = exception loop. WARNING/ERROR lines should be < 1% of INFO. |

## Failure → first response

| Symptom | Stop the soak? | First check | Likely cause |
|---------|---------------|-------------|--------------|
| RSS grew 50% in 6h | Yes | `gc.get_objects()` of one running worker via `py-spy dump` | Listener/handle leak in event loop or stats_cache holding payloads |
| FD count grew unbounded | Yes | `lsof -p <pid> \| awk '{print $5}' \| sort \| uniq -c \| sort -nr \| head` | Playwright (`pipe` for chromium) or unclosed aiohttp session |
| 5xx rate > 1% on /leads | No, but tail logs | Check if same path each time (single handler) or scattered | Single = regression; scattered = upstream (Supabase) flap |
| 429 rate non-zero | No | XFF injection working? Inspect a 429 response body | Locust VU XFF generator collision (unlikely, 24-bit space) or rate limiter misconfig |
| Render restart triggered | Yes | Capture the last 200 lines of Render log + dyno reason field | OOM kill or platform-side health probe failure |

## Post-soak analysis (T+24h)

1. Run `locust2html` against `leads_stats.csv` for distribution charts.
2. Compare p50/p95 from hour 1 vs hour 24 — drift > 25% = regression candidate.
3. Diff RSS at T-0 vs T+24h. < 10% delta = no leak. > 30% delta = leak; capture a heap snapshot for offline analysis.
4. Inspect the per-hour orchestrator JSON files — every one should show `status: started` and a UUID job_id.
5. Grep `slow handler:` in the backend log. Top 5 by total time = next perf-tuning candidates.
6. If clean: lock the current build as the "passed 24h soak" reference and tag it in git.

## Don'ts

- Don't run soak against prod Supabase if it has real customer data — every hour spawns an orchestrator job that writes lead rows. Use a separate Supabase project or accept the data churn.
- Don't share the soak's `LOAD_API_KEY` outside the dyno; it has full backend authority for 24h.
- Don't tune anything during the soak. Wait until it finishes (or fails decisively) — mid-run config changes invalidate the signal.
