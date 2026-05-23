#!/usr/bin/env bash
# Capture current quality metrics. Run this when intentionally updating
# a baseline after an improvement:
#
#   ./scripts/capture-quality-baselines.sh
#
# Reads the same argv arrays from .quality-baselines.json so adding a
# new tool only requires editing one file.
#
# Uses Python (not raw shell) to dispatch each tool — keeps shell
# expansion off the spawn surface and matches the comparator's
# subprocess.run(shell=False) discipline.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f .quality-baselines.json ]; then
  echo "ERROR: .quality-baselines.json not found in $REPO_ROOT" >&2
  exit 2
fi

python3 - <<'PY'
import json, subprocess, re, sys
from pathlib import Path

repo = Path.cwd()
baselines = json.loads((repo / ".quality-baselines.json").read_text())

def run(argv, cwd):
    try:
        r = subprocess.run(argv, cwd=str(cwd),
                           capture_output=True, text=True, timeout=600,
                           shell=False, check=False)
        return (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError as e:
        return f"ERROR: {e}"

parsers = {
    "ruff":          lambda o: re.search(r"Found (\d+) error", o),
    "mypy_strict":   lambda o: re.search(r"Found (\d+) error", o),
    "pylint_score":  lambda o: re.search(r"rated at (-?[0-9.]+)/10", o),
    "eslint":        lambda o: re.search(r"(\d+) problems?", o),
}

print(f"Current quality metrics ({repo})\n")
for metric, entry in baselines.items():
    if metric.startswith("_"): continue
    argv = entry.get("argv") or []
    cwd = (repo / entry.get("cwd", ".")).resolve()
    out = run(argv, cwd)
    if metric == "semgrep":
        try:
            n = len(json.loads(out).get("results", []))
        except Exception:
            n = "?"
        print(f"  {metric:15} {n}")
        continue
    m = parsers[metric](out)
    if m is None:
        print(f"  {metric:15} ? (parse failed)")
    else:
        print(f"  {metric:15} {m.group(1)}")
PY
