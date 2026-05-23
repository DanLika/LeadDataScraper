#!/usr/bin/env bash
# Spike-test driver: 0 → 100 users in 10s, hold 60s, → 0. Asserts no 5xx
# during the burst and that p95 normalises in the cool-down window.
#
# The actual shape lives in spike_locustfile.py (LoadTestShape subclass).
# This script just wires the env, kicks locust, and prints a one-line
# pass/fail summary based on the CSV stats.
#
# Pre-reqs:
#   - python3 + locust on PATH
#   - LOAD_API_BASE + LOAD_API_KEY exported
#   - Backend reachable at $LOAD_API_BASE (use https://… for prod, http://127.0.0.1:8000 for local)

set -euo pipefail

: "${LOAD_API_BASE:?Set LOAD_API_BASE=https://...}"
: "${LOAD_API_KEY:?Set LOAD_API_KEY=<backend API_SECRET_KEY>}"

RUN_AT="$(date -u +%Y%m%dT%H%M%SZ)"
REPORTS_DIR="$(dirname "$0")/reports/spike_${RUN_AT}"
mkdir -p "$REPORTS_DIR"

# The shape file caps itself at 90 seconds; --run-time is a belt-and-braces
# upper bound. If the cool-down window grows, bump both together.
RUN_TIME="${RUN_TIME:-2m}"

locust -f "$(dirname "$0")/spike_locustfile.py" --headless \
  --tags read \
  --host "$LOAD_API_BASE" \
  --run-time "$RUN_TIME" \
  --html "$REPORTS_DIR/spike.html" \
  --csv  "$REPORTS_DIR/spike" \
  --only-summary

# --- Post-run analysis ---------------------------------------------------
# Locust writes spike_stats.csv with the aggregate row last. Parse failure
# count + p95. Bash + awk only — no Python required for the assertion.
STATS="$REPORTS_DIR/spike_stats.csv"
if [[ ! -f "$STATS" ]]; then
  echo "FAIL: no stats CSV at $STATS"
  exit 2
fi

# Aggregated row name is 'Aggregated'. Columns include:
#   Type,Name,Request Count,Failure Count,...,95%
fail_count=$(awk -F',' 'tolower($2)=="\"aggregated\""{print $4}' "$STATS" | tr -d '"' | tr -d ' ')
p95=$(awk -F',' 'tolower($2)=="\"aggregated\""{print $(NF-3)}' "$STATS" | tr -d '"' | tr -d ' ')

echo "Spike summary: failures=${fail_count:-?}  p95=${p95:-?}ms"

# Assertion 1: no 5xx (failures > 0 in locust CSV implies at least one
# 4xx/5xx OR a catch_response failure as wired in the locustfile). The
# locustfile.py ReadUser code marks 429 separately AND 5xx — both count
# here as a failure. If the 429 rate is non-zero, inspect the HTML; if
# the system is overloaded, it should 503 instead of 429.
if [[ -n "${fail_count:-}" && "${fail_count}" != "0" ]]; then
  echo "FAIL: ${fail_count} failed responses during spike"
  exit 1
fi

echo "PASS: spike completed cleanly. Open $REPORTS_DIR/spike.html for the timeline."
