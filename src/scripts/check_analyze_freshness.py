"""ANALYZE freshness check.

``ANALYZE`` updates ``pg_statistic`` so the planner picks good plans.
Autovacuum's autoanalyze daemon usually keeps stats fresh, but it can
fall behind under steady write load — especially on tables with many
columns or large JSONB payloads (``leads.audit_results``).

Fails CI when:

- A core table with > ``ROW_THRESHOLD`` rows has both ``last_analyze``
  and ``last_autoanalyze`` NULL or older than ``STALE_AFTER_DAYS``.

NULL on either field means "never analyzed since stats reset"; an
ancient timestamp on both means autovacuum is throttled or disabled.

Report-only when:

- Any core table has either of the timestamps absent (NULL) but is
  below the row threshold — likely just a fresh table; not actionable.

For ad-hoc large writes (CSV upload), the backend should call
``ANALYZE leads`` immediately after the upload completes. See
``backend/main.py`` ``/upload`` handler — wire a post-success
``db.client.rpc('exec_analyze', {'tbl': 'leads'})`` if the bulk import
ever exceeds a few thousand rows. Without that, autoanalyze takes
minutes to catch up and the next query against the new rows runs on
stale stats.

Run via security.yml or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_analyze_freshness

Exit codes: 0 OK / 1 stale / 2 misconfig.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg

TABLES: tuple[str, ...] = (
    "leads", "campaigns", "campaign_messages", "orchestration_jobs",
)
TABLE_LIST = list(TABLES)
ROW_THRESHOLD = 10_000
STALE_AFTER_DAYS = 7


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
    report: list[str] = ["ANALYZE freshness report", "=========================="]
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS)

    try:
        cur = conn.execute(
            "SELECT relname, n_live_tup, last_analyze, last_autoanalyze "
            "FROM pg_stat_user_tables "
            "WHERE schemaname = 'public' AND relname = ANY(%s)",
            (TABLE_LIST,),
        )
        rows = cur.fetchall()
    except psycopg.Error as e:
        print(f"ERROR: pg_stat_user_tables query failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    found = {row[0] for row in rows}
    for missing in sorted(set(TABLES) - found):
        report.append(f"  {missing}: not in pg_stat_user_tables (table missing?)")

    for table, n_live, last_analyze, last_autoanalyze in sorted(rows):
        # The freshest of the two timestamps; either being recent is fine.
        freshest = max(
            (t for t in (last_analyze, last_autoanalyze) if t is not None),
            default=None,
        )
        n_live = int(n_live or 0)
        report.append(
            f"  {table}: rows={n_live:>9}  last_analyze={last_analyze} "
            f"  last_autoanalyze={last_autoanalyze}"
        )
        if n_live > ROW_THRESHOLD:
            if freshest is None:
                failures.append(
                    f"public.{table}: {n_live} rows but never analyzed "
                    f"— run ANALYZE public.{table};"
                )
            elif freshest < cutoff:
                failures.append(
                    f"public.{table}: {n_live} rows, last analyzed "
                    f"{freshest.isoformat()} (>{STALE_AFTER_DAYS}d ago) "
                    f"— autovacuum may be throttled, run ANALYZE."
                )

    print("\n".join(report))

    if failures:
        print("\nANALYZE freshness FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(
        f"\nANALYZE freshness PASSED "
        f"(threshold: rows > {ROW_THRESHOLD}, stale after {STALE_AFTER_DAYS}d)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
