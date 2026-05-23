#!/usr/bin/env python3
"""Quality-baseline ratchet comparator.

Runs each tool listed in `.quality-baselines.json`, parses its count or
score, compares against the committed baseline, and exits non-zero if
any current value REGRESSES (worse than the baseline).

Default direction is lower-is-better (`operator: "lte"`, the default).
For metrics where higher is better (e.g. pylint score), set
`"operator": "gte"` in the baseline entry.

Exit codes:
    0 — every metric is at or better than the baseline
    1 — at least one metric regressed
    2 — a tool failed to run or a baseline entry is malformed

The comparator does NOT auto-update the baseline. When a metric
improves (lower than baseline), the script prints a one-line
"baseline can drop to N — `git add .quality-baselines.json`" hint and
exits 0; the human author rolls the file forward in the same PR.

Each baseline entry's `argv` is passed directly to `subprocess.run`
with `shell=False`. No shell interpolation, no command-injection
surface (CWE-78). Use the entry's `cwd` field to set the working
directory instead of `cd X && ...`.

Usage:
    python scripts/check-quality-baselines.py            # current dir
    python scripts/check-quality-baselines.py --repo /path/to/repo
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run `argv` in `cwd` without a shell. Capture combined stdout+stderr.
    Never raise — a non-zero exit code is data, not an error (most
    linters exit non-zero when they find issues).

    Tool resolution comes from PATH (no shell expansion). Each baseline
    argv lists the tool by name; CI installs them globally so the lookup
    finds them. Pre-validates that `argv` is a non-empty list of strings
    so a malformed baseline file can't sneak a non-string into the
    `subprocess.run` argv parameter."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError(f"argv must be a non-empty list of strings, got {argv!r}")
    result = subprocess.run(  # noqa: S603 — argv is shell=False; CWE-78 N/A
        argv, cwd=str(cwd),
        capture_output=True, text=True, timeout=600,
        shell=False, check=False,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _parse_ruff(output: str) -> int | None:
    """ruff prints 'Found N errors' (or 'All checks passed!' for 0)."""
    m = re.search(r"Found (\d+) error", output)
    if m:
        return int(m.group(1))
    if "All checks passed" in output:
        return 0
    return None


def _parse_mypy(output: str) -> int | None:
    """mypy summary: 'Found N errors in M files (checked X source files)'
    or 'Success: no issues found' for 0."""
    m = re.search(r"Found (\d+) error", output)
    if m:
        return int(m.group(1))
    if "Success: no issues found" in output:
        return 0
    return None


def _parse_pylint(output: str) -> float | None:
    """pylint prints 'Your code has been rated at X.XX/10'."""
    m = re.search(r"rated at (-?[0-9.]+)/10", output)
    return float(m.group(1)) if m else None


def _parse_eslint(output: str) -> int | None:
    """eslint prints 'X problems (Y errors, Z warnings)' or empty on 0."""
    m = re.search(r"(\d+) problems?", output)
    if m:
        return int(m.group(1))
    # eslint --max-warnings 0 exits non-zero with NO problems line when
    # there are simply no warnings — the absence of "problem" + the
    # absence of error messages means 0.
    if not output.strip():
        return 0
    if "error" not in output.lower() and "warning" not in output.lower():
        return 0
    return None


def _parse_semgrep(output: str) -> int | None:
    """semgrep --json returns `{"results": [...]}`. Falls back to the
    text-mode "N findings" line if `--json` wasn't requested."""
    try:
        data = json.loads(output)
        return len(data.get("results", []))
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"(\d+) finding", output)
    return int(m.group(1)) if m else None


_PARSERS: dict[str, Any] = {
    "ruff": _parse_ruff,
    "mypy_strict": _parse_mypy,
    "pylint_score": _parse_pylint,
    "eslint": _parse_eslint,
    "semgrep": _parse_semgrep,
}


def _compare(metric: str, baseline: Any, current: Any, operator: str) -> tuple[str, str]:
    """Return (status, message) where status is 'pass' / 'fail' / 'improvement'."""
    if operator == "gte":
        # Higher is better — fail if current < baseline.
        if current < baseline:
            return "fail", f"  ❌ {metric}: regressed {baseline} → {current} (lower is worse)"
        if current > baseline:
            return "improvement", f"  ✨ {metric}: improved {baseline} → {current} — `git add .quality-baselines.json` to lock in"
        return "pass", f"  ✓ {metric}: {current} (== baseline)"

    # Default: lower is better.
    if current > baseline:
        return "fail", f"  ❌ {metric}: regressed {baseline} → {current} (+{current - baseline})"
    if current < baseline:
        return "improvement", f"  ✨ {metric}: improved {baseline} → {current} (-{baseline - current}) — `git add .quality-baselines.json` to lock in"
    return "pass", f"  ✓ {metric}: {current} (== baseline)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root (default: CWD)")
    parser.add_argument("--baselines", type=Path, default=Path(".quality-baselines.json"))
    args = parser.parse_args()

    repo = args.repo.resolve()
    baselines_path = repo / args.baselines
    if not baselines_path.exists():
        print(f"ERROR: baselines file not found: {baselines_path}", file=sys.stderr)
        return 2

    baselines = json.loads(baselines_path.read_text())
    print(f"Quality ratchet ({repo})")
    print(f"Baselines: {baselines_path.relative_to(repo)}\n")

    any_failed = False
    for metric, entry in baselines.items():
        if metric.startswith("_"):
            continue
        argv: list[str] = entry.get("argv") or []
        baseline: Any = entry["value"]
        operator: str = entry.get("operator", "lte")
        cwd_rel = entry.get("cwd", ".")
        cwd = (repo / cwd_rel).resolve()

        parser_fn = _PARSERS.get(metric)
        if parser_fn is None:
            print(f"  ⚠️  {metric}: no parser registered — skipping")
            continue

        # Short label for the run line; never `print(argv)` with eval —
        # this is purely cosmetic.
        label = " ".join(argv[:2]) + (" ..." if len(argv) > 2 else "")
        print(f"  · {metric}: running `{label}` (cwd={cwd_rel})")
        try:
            _, output = _run(argv, cwd)
        except (FileNotFoundError, ValueError) as exc:
            print(f"  ❌ {metric}: tool failed to launch — {exc}")
            any_failed = True
            continue

        current = parser_fn(output)
        if current is None:
            print(f"  ❌ {metric}: failed to parse output (see below)")
            print("    " + "\n    ".join(output.strip().splitlines()[-5:]))
            any_failed = True
            continue

        status, message = _compare(metric, baseline, current, operator)
        print(message)
        if status == "fail":
            any_failed = True

    print()
    if any_failed:
        print("FAIL — one or more metrics regressed. Fix the underlying finding; do not raise the baseline.")
        return 1
    print("PASS — every metric at or better than baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
