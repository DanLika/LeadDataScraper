"""Slow-query report from ``pg_stat_statements``.

Three sections, all read-only against the live DB:

1. **Top 10 by total_exec_time** — biggest consumers of CPU. The
   #1 query is the one to look at first; even a 50ms query dominates
   if it runs 10M times/day.

2. **Queries with mean_exec_time > 1 s** — slow enough to be felt by a
   human; index opportunity or query rewrite.

3. **Cache hit ratio < 99% on hot queries** — ``shared_blks_hit /
   (shared_blks_hit + shared_blks_read)``. Anything frequently
   missing the buffer cache is a candidate for an index that fits
   in memory or for a covering index. Limit to queries with
   ``calls`` > 100 so we don't chase one-off seqscans.

Read-only, idempotent; safe to run on a schedule. ``pg_stat_statements``
is enabled by default on Supabase (v1.11 in this project).

Run via security.yml (weekly cadence) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.slow_query_report

Exit codes:
    0 = no anomalies found (report always printed)
    1 = at least one query exceeded a threshold
    2 = misconfigured run or extension missing
"""

from __future__ import annotations

import os
import sys

import psycopg

# Anything above this gets flagged as "slow per call".
MEAN_EXEC_SLOW_MS = 1000.0
# Cache hit ratio below this on hot queries gets flagged.
CACHE_HIT_FLOOR = 0.99
# Calls threshold for "hot" — below this we don't care about cache
# ratio (a single seqscan looks bad but is just noise).
HOT_QUERY_MIN_CALLS = 100
TOP_N = 10
# pg_stat_statements truncates query text at this length by default
# (track_activity_query_size = 1024). Display max — long ones are
# clipped in the report; the operator can pull full text by hash.
QUERY_PREVIEW_LEN = 160


def _ensure_extension(conn: psycopg.Connection) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
    )
    return cur.fetchone() is not None


def _preview(query_text: str) -> str:
    flat = " ".join(query_text.split())
    return flat if len(flat) <= QUERY_PREVIEW_LEN else flat[:QUERY_PREVIEW_LEN] + "…"


def _analyze_top_queries(conn: psycopg.Connection, report: list[str]) -> None:
    # --- Section 1: top by total_exec_time ---
    # LIKE patterns bound as parameters — inline `%` in 'EXPLAIN%' /
    # '%pg_stat_statements%' collides with psycopg's placeholder
    # parser (only %s/%b/%t allowed), which is how this gate has
    # been failing silently since it landed. Param binding side-
    # steps the parser entirely.
    cur = conn.execute(
        "SELECT calls, total_exec_time, mean_exec_time, query "
        "FROM pg_stat_statements "
        "WHERE query NOT ILIKE %s "
        "  AND query NOT ILIKE %s "
        "ORDER BY total_exec_time DESC "
        "LIMIT %s",
        ("EXPLAIN%", "%pg_stat_statements%", TOP_N),
    )
    report.append("")
    report.append(f"  Top {TOP_N} by total_exec_time:")
    for calls, total_ms, mean_ms, qtext in cur.fetchall():
        report.append(
            f"    total={float(total_ms):>10.0f} ms  "
            f"mean={float(mean_ms):>7.1f} ms  "
            f"calls={int(calls):>7}  {_preview(qtext)}"
        )


def _analyze_slow_queries(conn: psycopg.Connection, report: list[str], findings: list[str]) -> None:
    # --- Section 2: mean_exec_time > 1s ---
    cur = conn.execute(
        "SELECT calls, mean_exec_time, query "
        "FROM pg_stat_statements "
        "WHERE mean_exec_time > %s "
        "  AND query NOT ILIKE %s "
        "  AND query NOT ILIKE %s "
        "ORDER BY mean_exec_time DESC",
        (MEAN_EXEC_SLOW_MS, "EXPLAIN%", "%pg_stat_statements%"),
    )
    slow_rows = cur.fetchall()
    report.append("")
    report.append(f"  Queries with mean_exec_time > {MEAN_EXEC_SLOW_MS:.0f} ms:")
    if not slow_rows:
        report.append("    (none)")
    for calls, mean_ms, qtext in slow_rows:
        report.append(
            f"    mean={float(mean_ms):>7.1f} ms  calls={int(calls):>7}  "
            f"{_preview(qtext)}"
        )
        findings.append(
            f"slow query (mean {float(mean_ms):.0f}ms, called "
            f"{int(calls)}x): {_preview(qtext)}"
        )


def _analyze_cold_queries(conn: psycopg.Connection, report: list[str], findings: list[str]) -> None:
    # --- Section 3: low cache hit ratio on hot queries ---
    cur = conn.execute(
        "SELECT calls, mean_exec_time, "
        "       shared_blks_hit, shared_blks_read, query "
        "FROM pg_stat_statements "
        "WHERE calls >= %s "
        "  AND (shared_blks_hit + shared_blks_read) > 0 "
        "  AND query NOT ILIKE %s "
        "  AND query NOT ILIKE %s",
        (HOT_QUERY_MIN_CALLS, "EXPLAIN%", "%pg_stat_statements%"),
    )
    cold_rows: list[tuple[float, int, float, str]] = []
    for calls, mean_ms, hit, read, qtext in cur.fetchall():
        denom = int(hit) + int(read)
        if denom == 0:
            continue
        ratio = int(hit) / denom
        if ratio < CACHE_HIT_FLOOR:
            cold_rows.append((ratio, int(calls), float(mean_ms), qtext))
    cold_rows.sort()  # lowest ratio first
    report.append("")
    report.append(
        f"  Hot queries (calls ≥ {HOT_QUERY_MIN_CALLS}) with cache hit ratio "
        f"< {CACHE_HIT_FLOOR:.0%}:"
    )
    if not cold_rows:
        report.append("    (none)")
    for ratio, calls, mean_ms, qtext in cold_rows:
        report.append(
            f"    cache_hit={ratio:>6.2%}  calls={calls:>7}  "
            f"mean={mean_ms:>6.1f}ms  {_preview(qtext)}"
        )
        findings.append(
            f"cold hot-path query (cache_hit={ratio:.1%}, "
            f"{calls} calls): index opportunity for "
            f"{_preview(qtext)}"
        )


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

    findings: list[str] = []
    report: list[str] = ["Slow query report", "================="]

    try:
        if not _ensure_extension(conn):
            print(
                "ERROR: pg_stat_statements extension is not installed. "
                "Run CREATE EXTENSION pg_stat_statements; in Supabase Studio.",
                file=sys.stderr,
            )
            return 2

        _analyze_top_queries(conn, report)
        _analyze_slow_queries(conn, report, findings)
        _analyze_cold_queries(conn, report, findings)
    except psycopg.Error as e:
        print(f"ERROR: pg_stat_statements query failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    print("\n".join(report))

    if findings:
        print("\nSlow query findings:", file=sys.stderr)
        for line in findings:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("\nSlow query report: no anomalies above thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
