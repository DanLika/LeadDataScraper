"""Storage size report — weekly cadence.

Supabase enforces per-project DB storage quotas (Free: 500 MB,
Pro: 8 GB by default, scales with disk). Crossing the cap blocks
writes; better to notice growth weeks before that.

Three blocks:

1. **Database size**: ``pg_database_size(current_database())``. Set
   ``STORAGE_QUOTA_BYTES`` via env to drive the warn/fail thresholds
   (default = 8 GiB matching Supabase Pro base disk). FAIL above
   ``HARD_FAIL_RATIO`` (90% by default), WARN above
   ``SOFT_WARN_RATIO`` (70% by default).

2. **Per-table breakdown**: ``pg_total_relation_size`` for every core
   table (heap + indexes + TOAST). Sorted largest first — the
   operator's archiving punch list. ``audit_results`` JSONB is the
   prime suspect once volume builds.

3. **Top growth this week** — diff against a baseline saved by the
   previous run. Baseline lives in ``STORAGE_BASELINE_PATH``
   (default ``./.storage_baseline.json``); CI persists it via
   ``actions/cache@v4`` keyed on the workflow + month. First run
   reports "(no baseline)" and writes one; subsequent runs print
   per-table delta. Anomalous growth (>2x WoW on any table)
   contributes to FAIL.

Run via security.yml or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.storage_report

Exit codes:
    0 = under all thresholds
    1 = soft warn (70%+) OR anomalous growth (>2x WoW any table)
    2 = misconfigured run
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

TABLES: tuple[str, ...] = (
    "leads",
    "campaigns",
    "campaign_messages",
    "orchestration_jobs",
)
TABLE_LIST = list(TABLES)
DEFAULT_QUOTA_BYTES = 8 * 1024**3  # 8 GiB — Supabase Pro base disk
SOFT_WARN_RATIO = 0.70
HARD_FAIL_RATIO = 0.90
WOW_ANOMALY_MULTIPLIER = 2.0  # >2x growth WoW = suspicious
DEFAULT_BASELINE_PATH = "./.storage_baseline.json"


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    quota = int(os.environ.get("STORAGE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES))
    baseline_path = Path(os.environ.get("STORAGE_BASELINE_PATH", DEFAULT_BASELINE_PATH))

    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    findings: list[str] = []
    report: list[str] = ["Storage size report", "===================="]

    try:
        cur = conn.execute("SELECT pg_database_size(current_database())")
        db_bytes = int(cur.fetchone()[0])
        ratio = db_bytes / quota if quota else 0.0
        report.append(
            f"  database total: {_human_bytes(db_bytes)} "
            f"({ratio:.1%} of {_human_bytes(quota)} quota)"
        )
        if ratio >= HARD_FAIL_RATIO:
            findings.append(
                f"DB at {ratio:.1%} of {_human_bytes(quota)} quota — "
                f"HARD threshold {HARD_FAIL_RATIO:.0%}: upgrade plan or "
                f"archive immediately"
            )
        elif ratio >= SOFT_WARN_RATIO:
            findings.append(
                f"DB at {ratio:.1%} of {_human_bytes(quota)} quota — "
                f"crossing soft threshold {SOFT_WARN_RATIO:.0%}: plan "
                f"upgrade or archival within the next quarter"
            )

        # Per-table breakdown
        cur = conn.execute(
            "SELECT relname, "
            "       pg_total_relation_size(('public.'||relname)::regclass) AS bytes "
            "FROM pg_stat_user_tables "
            "WHERE schemaname = 'public' AND relname = ANY(%s) "
            "ORDER BY pg_total_relation_size(('public.'||relname)::regclass) DESC",
            (TABLE_LIST,),
        )
        current: dict[str, int] = {r[0]: int(r[1]) for r in cur.fetchall()}

        report.append("")
        report.append(f"  {'table':<22} {'size':>12} {'wow_delta':>16}")
        report.append("  " + "-" * 52)

        baseline: dict[str, int] = {}
        if baseline_path.is_file():
            try:
                with baseline_path.open() as fh:
                    raw = json.load(fh)
                baseline = {k: int(v) for k, v in raw.get("tables", {}).items()}
            except (json.JSONDecodeError, ValueError, OSError):
                report.append(
                    f"  (baseline at {baseline_path} unreadable — "
                    f"treating as first run)"
                )

        for table, bytes_now in current.items():
            prev = baseline.get(table)
            if prev is None or prev == 0:
                delta_str = "(no baseline)"
            else:
                ratio_growth = bytes_now / prev
                delta_str = f"{(ratio_growth - 1.0):+.1%}"
                if ratio_growth > WOW_ANOMALY_MULTIPLIER:
                    findings.append(
                        f"public.{table}: WoW growth "
                        f"{(ratio_growth - 1.0):+.1%} (was "
                        f"{_human_bytes(prev)}, now "
                        f"{_human_bytes(bytes_now)}) — exceeds "
                        f"{WOW_ANOMALY_MULTIPLIER:.0f}x — suggests stuck "
                        f"job inserting forever, runaway producer, or "
                        f"missed cleanup"
                    )
            report.append(
                f"  {table:<22} {_human_bytes(bytes_now):>12} {delta_str:>16}"
            )

        # Persist new baseline for next run
        try:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            with baseline_path.open("w") as fh:
                json.dump(
                    {"tables": current, "db_total_bytes": db_bytes},
                    fh,
                    indent=2,
                )
        except OSError as e:
            report.append(f"  (could not persist baseline to {baseline_path}: {e})")
    except psycopg.Error as e:
        print(f"ERROR: storage probe failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    print("\n".join(report))

    if findings:
        print("\nStorage report findings:", file=sys.stderr)
        for line in findings:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("\nStorage report: no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
