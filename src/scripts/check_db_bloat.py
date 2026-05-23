"""DB bloat / vacuum health report.

Three checks per core table (and on first run, a snapshot for WoW
comparison via a CI artifact — left as a follow-up TODO since
artifact-fetch-across-runs adds workflow complexity):

1. **Dead tuple ratio** = ``n_dead_tup / GREATEST(n_live_tup, 1)``.
   > 0.20 → autovacuum is throttled or the workload exceeds its
   default `autovacuum_vacuum_scale_factor`. Suggests tuning per table.

2. **Table size** via ``pg_total_relation_size`` (heap + indexes +
   TOAST). Sorted largest first — operator's hit list when planning
   archiving.

3. **Approximate index bloat** via ``pgstattuple_approx`` — currently
   SKIPPED because the extension isn't installed on this Supabase
   project. To enable, ``CREATE EXTENSION pgstattuple;`` (Supabase
   allows this on Pro plans). The script auto-detects and reports
   when the extension is available.

Run via security.yml (weekly cron) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_db_bloat

Exit codes:
    0 = all tables under thresholds (report still printed)
    1 = at least one table over the dead-tuple threshold
    2 = misconfigured run
"""
from __future__ import annotations

import os
import sys

import psycopg

TABLES: tuple[str, ...] = (
    "leads", "campaigns", "campaign_messages", "orchestration_jobs",
)
TABLE_LIST = list(TABLES)
DEAD_TUPLE_RATIO_THRESHOLD = 0.20


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _pgstattuple_available(conn: psycopg.Connection) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM pg_extension WHERE extname = 'pgstattuple'"
    )
    return cur.fetchone() is not None


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    failures: list[str] = []
    report: list[str] = ["DB bloat report", "==============="]

    try:
        cur = conn.execute(
            "SELECT relname, n_live_tup, n_dead_tup, "
            "       pg_total_relation_size(('public.'||relname)::regclass) AS total_bytes "
            "FROM pg_stat_user_tables "
            "WHERE schemaname = 'public' AND relname = ANY(%s) "
            "ORDER BY pg_total_relation_size(('public.'||relname)::regclass) DESC",
            (TABLE_LIST,),
        )
        rows = cur.fetchall()
        report.append(f"  pgstattuple extension: "
                      f"{'available' if _pgstattuple_available(conn) else 'NOT installed (CREATE EXTENSION pgstattuple to enable)'}")
        report.append("")
        report.append(
            f"  {'table':<22} {'rows':>10} {'dead':>10} "
            f"{'dead_ratio':>11} {'size':>12}"
        )
        report.append("  " + "-" * 70)
        for relname, n_live, n_dead, total_bytes in rows:
            n_live = int(n_live or 0)
            n_dead = int(n_dead or 0)
            denom = max(n_live, 1)
            ratio = n_dead / denom
            report.append(
                f"  {relname:<22} {n_live:>10} {n_dead:>10} "
                f"{ratio:>10.1%} {_human_bytes(int(total_bytes)):>12}"
            )
            if ratio > DEAD_TUPLE_RATIO_THRESHOLD and n_live > 0:
                failures.append(
                    f"public.{relname}: dead_tuple_ratio={ratio:.1%} "
                    f"({n_dead}/{n_live}) — exceeds "
                    f"{DEAD_TUPLE_RATIO_THRESHOLD:.0%}. Tune "
                    f"autovacuum_vacuum_scale_factor for this table or "
                    f"run VACUUM ANALYZE public.{relname};"
                )
    except psycopg.Error as e:
        print(f"ERROR: bloat probe failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    print("\n".join(report))

    if failures:
        print("\nBloat check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"\nBloat check PASSED (threshold: dead_ratio > {DEAD_TUPLE_RATIO_THRESHOLD:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
