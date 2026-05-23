"""Aggregate flaky-test signal from three parallel pytest runs.

A test is FLAKY if its outcome differs across the three runs (e.g. PASS in
one, FAIL in another) OR if pytest-rerunfailures recorded a passing-after-
rerun for it within a single run. This catches the two classic shapes:
non-deterministic test bodies AND timing-sensitive externals (Gemini API,
Playwright, the Supabase test branch).

Reads three pytest-json-report files at ``--report run-1.json run-2.json
run-3.json``. Optionally merges with a previous ``flaky-tests.json`` from
``--previous`` so the file accumulates history across nightly runs.
Entries older than ``--retention-days`` (default 14) are dropped.

Output JSON shape:

    {
      "generated_at": "2026-05-22T00:00:00Z",
      "retention_days": 14,
      "flakes": [
        {
          "test_id": "tests/test_orchestrator.py::test_run_pipeline_smoke",
          "file": "tests/test_orchestrator.py",
          "first_seen": "2026-05-15",
          "last_seen": "2026-05-22",
          "outcomes_today": ["passed", "failed", "passed"],
          "occurrences": 4
        },
        ...
      ]
    }

The PR-time ``flaky-gate`` job in ci.yml reads this file and blocks merge
when a PR's changed-files set intersects the ``file`` field of any entry
whose ``last_seen`` is within the last 7 days.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _load_report(path: Path) -> dict[str, str]:
    """Return ``{test_id: outcome}`` from a pytest-json-report file."""
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for test in data.get("tests", []):
        nodeid = test["nodeid"]
        # pytest-rerunfailures reports `outcome` as the final outcome.
        # The per-stage `setup`/`call`/`teardown` blocks carry "rerun"
        # outcomes when the test was retried — surface them so a
        # passing-after-rerun still counts as flaky.
        outcome = test.get("outcome", "unknown")
        for stage in ("setup", "call", "teardown"):
            stage_outcome = test.get(stage, {}).get("outcome")
            if stage_outcome == "rerun":
                outcome = "flaky-rerun"
                break
        out[nodeid] = outcome
    return out


def _todays_flakes(runs: list[dict[str, str]]) -> dict[str, list[str]]:
    """Return ``{test_id: outcomes}`` for every test with mixed outcomes."""
    all_ids: set[str] = set()
    for run in runs:
        all_ids.update(run.keys())

    flaky: dict[str, list[str]] = {}
    for nodeid in all_ids:
        outcomes = [run.get(nodeid, "missing") for run in runs]
        # Mixed = at least one PASS and at least one non-PASS, OR any
        # rerun-marker. Treat "missing" as ignored — a test only present
        # in some runs is a collection issue, not a flake.
        present = [o for o in outcomes if o != "missing"]
        if not present:
            continue
        has_pass = any(o == "passed" for o in present)
        has_fail = any(o in ("failed", "error", "flaky-rerun") for o in present)
        if has_pass and has_fail:
            flaky[nodeid] = outcomes
    return flaky


def _test_id_to_file(test_id: str) -> str:
    """``tests/test_foo.py::test_bar[param]`` -> ``tests/test_foo.py``."""
    return test_id.split("::", 1)[0]


def _merge_with_history(
    todays: dict[str, list[str]],
    previous: dict[str, Any] | None,
    retention_days: int,
) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
    existing: dict[str, dict[str, Any]] = {}
    if previous is not None:
        for entry in previous.get("flakes", []):
            if entry.get("last_seen", "0000-00-00") >= cutoff:
                existing[entry["test_id"]] = entry

    for test_id, outcomes in todays.items():
        entry = existing.get(test_id)
        if entry is None:
            existing[test_id] = {
                "test_id": test_id,
                "file": _test_id_to_file(test_id),
                "first_seen": today,
                "last_seen": today,
                "outcomes_today": outcomes,
                "occurrences": 1,
            }
        else:
            entry["last_seen"] = today
            entry["outcomes_today"] = outcomes
            entry["occurrences"] = int(entry.get("occurrences", 0)) + 1

    # Stable sort: oldest first-seen first, then alphabetical by id.
    return sorted(
        existing.values(),
        key=lambda e: (e["first_seen"], e["test_id"]),
    )


def _build_issue_body(flakes: Iterable[dict[str, Any]], gate_days: int) -> str:
    flakes = list(flakes)
    if not flakes:
        return (
            "_No flaky tests detected in the last retention window._\n\n"
            "This issue stays open as the canonical tracker. It is "
            "automatically updated by `.github/workflows/flakiness-detector.yml`.\n"
        )

    cutoff = (date.today() - timedelta(days=gate_days)).isoformat()
    gate_active = [f for f in flakes if f["last_seen"] >= cutoff]

    lines = [
        f"_Auto-generated by `flakiness-detector.yml` on "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}. "
        f"PR-merge gate is **active** for entries newer than "
        f"{gate_days} days._\n",
        "## Active gate (block-merge if PR touches these files)\n",
    ]
    if not gate_active:
        lines.append("_No tests flagged in the last "
                     f"{gate_days} days. Gate inactive._\n")
    else:
        lines.append("| Test | File | First seen | Last seen | Occurrences |")
        lines.append("|---|---|---|---|---|")
        for f in gate_active:
            lines.append(
                f"| `{f['test_id']}` "
                f"| `{f['file']}` "
                f"| {f['first_seen']} "
                f"| {f['last_seen']} "
                f"| {f['occurrences']} |"
            )

    historical = [f for f in flakes if f["last_seen"] < cutoff]
    if historical:
        lines.append("\n## Historical (within retention, no longer gating)\n")
        lines.append("| Test | File | First seen | Last seen | Occurrences |")
        lines.append("|---|---|---|---|---|")
        for f in historical:
            lines.append(
                f"| `{f['test_id']}` "
                f"| `{f['file']}` "
                f"| {f['first_seen']} "
                f"| {f['last_seen']} "
                f"| {f['occurrences']} |"
            )

    lines.append("\n## How to clear an entry\n")
    lines.append(
        "1. Fix the underlying flake — usually network/timing or "
        "non-deterministic test data.\n"
        "2. Wait for the next nightly run. If the test no longer flips "
        "outcome across the three parallel runs, its `last_seen` will "
        "stop updating and it will age out of the gate after 7 days "
        "(falls into the *Historical* section), then drop off entirely "
        "after the retention window.\n"
        "3. To remove immediately, edit `flaky-tests.json` in the "
        "tracking gist and delete the entry.\n"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", required=True, type=Path,
                        help="pytest-json-report files from the parallel runs")
    parser.add_argument("--previous", type=Path,
                        help="prior flaky-tests.json to merge with (optional)")
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--gate-days", type=int, default=7,
                        help="window used by ci.yml flaky-gate")
    parser.add_argument("--out", type=Path, required=True,
                        help="path to write flaky-tests.json")
    parser.add_argument("--issue-body-out", type=Path,
                        help="optional path to write the markdown issue body")
    args = parser.parse_args()

    runs = [_load_report(p) for p in args.reports]
    todays = _todays_flakes(runs)

    previous = None
    if args.previous and args.previous.exists():
        try:
            previous = json.loads(args.previous.read_text())
        except json.JSONDecodeError:
            print(f"Warning: {args.previous} not valid JSON; starting fresh",
                  file=sys.stderr)

    flakes = _merge_with_history(todays, previous, args.retention_days)

    output = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "retention_days": args.retention_days,
        "gate_days": args.gate_days,
        "flakes": flakes,
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(f"Wrote {len(flakes)} flake entries to {args.out}")

    if args.issue_body_out:
        args.issue_body_out.write_text(
            _build_issue_body(flakes, args.gate_days)
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
