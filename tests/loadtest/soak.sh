#!/usr/bin/env bash
# 24h soak test against the LeadDataScraper backend.
#
# Sustained, MODERATE load — not a burst. The goal isn't to stress the
# system, it's to surface *slow* failure modes that 5-min burst tests
# never see: memory leaks, file-descriptor leaks (e.g. a Playwright
# browser someone forgot to close), DB connection pool starvation,
# unbounded log growth, Render auto-restart loops.
#
# Targets:
#   - 5 RPS sustained on /leads
#   - 1 RPS sustained on /stats
#   - 1 orchestrator job per hour (started + polled)
#
# Watch separately (NOT in this script — operator side-channel):
#   - Render dashboard: dyno restart count, RSS over time, CPU
#   - `lsof -p $(pgrep -f uvicorn) | wc -l`   on a local repro
#   - Supabase dashboard → Database → Pool utilisation
#
# Run modes:
#   ./soak.sh                  → 24h default, requires LOAD_API_BASE + LOAD_API_KEY
#   ./soak.sh 1h               → custom duration, useful for smoke
#   DRY_RUN=1 ./soak.sh        → print commands, don't execute (review before kicking off)
#
# Stop early: Ctrl-C is honored, locust shuts down cleanly via the spawn-rate
# ramp-down. The orchestrator polling loop catches the kill signal too.
#
# IMPORTANT: the locustfile's synthetic X-Forwarded-For per VU still
# applies, so per-IP rate limits don't collapse the throughput target.

set -euo pipefail

DURATION="${1:-24h}"
RUN_AT="$(date -u +%Y%m%dT%H%M%SZ)"
REPORTS_DIR="$(dirname "$0")/reports/soak_${RUN_AT}"
mkdir -p "$REPORTS_DIR"

: "${LOAD_API_BASE:?Set LOAD_API_BASE=https://...}"
: "${LOAD_API_KEY:?Set LOAD_API_KEY=<backend API_SECRET_KEY>}"

LEADS_USERS=5    # ≈ 5 rps with constant_throughput(1.0) per-user
STATS_USERS=1    # ≈ 1 rps
ORCH_INTERVAL_SECONDS=3600  # 1 job/hr

LOCUSTFILE="$(dirname "$0")/locustfile.py"

run() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# ---- /leads soak --------------------------------------------------------
run env LOAD_API_BASE="$LOAD_API_BASE" LOAD_API_KEY="$LOAD_API_KEY" \
  locust -f "$LOCUSTFILE" --headless \
  --tags read \
  --users "$LEADS_USERS" --spawn-rate "$LEADS_USERS" \
  --run-time "$DURATION" \
  --host "$LOAD_API_BASE" \
  --html "$REPORTS_DIR/leads.html" \
  --csv  "$REPORTS_DIR/leads" \
  --only-summary &
LEADS_PID=$!

# ---- /stats soak --------------------------------------------------------
run env LOAD_API_BASE="$LOAD_API_BASE" LOAD_API_KEY="$LOAD_API_KEY" \
  locust -f "$LOCUSTFILE" --headless \
  --tags stats \
  --users "$STATS_USERS" --spawn-rate "$STATS_USERS" \
  --run-time "$DURATION" \
  --host "$LOAD_API_BASE" \
  --html "$REPORTS_DIR/stats.html" \
  --csv  "$REPORTS_DIR/stats" \
  --only-summary &
STATS_PID=$!

# ---- 1 orchestrator job per hour ---------------------------------------
# A trickle of writes so the write path stays exercised (otherwise the
# soak would only confirm the read path is leak-free).
(
  while true; do
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
      printf '[dry-run] curl POST %s/orchestrator/start\n' "$LOAD_API_BASE"
    else
      curl -s -o "$REPORTS_DIR/orchestrator_$(date -u +%H%M%SZ).json" \
        -X POST "$LOAD_API_BASE/orchestrator/start" \
        -H "X-API-Key: $LOAD_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"tasks":["audit"]}' || true
    fi
    sleep "$ORCH_INTERVAL_SECONDS"
  done
) &
ORCH_PID=$!

cleanup() {
  echo "Shutting down soak ($DURATION elapsed or interrupted)…"
  kill "$ORCH_PID" 2>/dev/null || true
  wait "$LEADS_PID" 2>/dev/null || true
  wait "$STATS_PID" 2>/dev/null || true
  echo "Reports in $REPORTS_DIR"
}
trap cleanup EXIT INT TERM

wait "$LEADS_PID" "$STATS_PID"
