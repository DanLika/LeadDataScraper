"""Suggest GIN / expression indexes for JSONB hot paths.

Two real JSONB columns: ``leads.audit_results`` and
``orchestration_jobs.filters``. (The columns named in earlier task
specs as ``business_details`` / ``contact_details`` are TEXT, not
JSONB — Gemini-generated prose, no JSON structure to index.)

This script is **analysis only** — does not create indexes. Run it
weekly; if a recommendation shows up multiple weeks in a row, that's
the signal to actually ``CREATE INDEX``.

Heuristics, scored against ``pg_stat_statements``:

- Containment (``@>``) or key-existence (``?``, ``?|``, ``?&``)
  predicates on ``audit_results`` or ``filters`` with mean_exec_time
  > 50ms or aggregate total_exec_time among the top 20 →
  **suggest** ``CREATE INDEX ON <table> USING gin (<column>)``.
- Specific key extraction (``column->>'key'`` or
  ``column->'key'``) used in WHERE / ORDER BY with mean > 50ms →
  **suggest** ``CREATE INDEX ON <table> ((<column>->>'<key>'))``.

Output is a human-readable suggestion list. Existing indexes are
listed first so the operator can verify a suggestion isn't already in
place under a different name.

Run via security.yml (weekly) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.suggest_jsonb_indexes

Exit codes:
    0 = analysis complete (suggestions printed; exit 0 even with
        recommendations — this is advisory only, never gating)
    2 = misconfigured run or extension missing
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from typing import cast

import psycopg

JSONB_TARGETS: tuple[tuple[str, str], ...] = (
    ("leads", "audit_results"),
    ("orchestration_jobs", "filters"),
)

# Operator patterns that benefit from a GIN index on the whole column.
GIN_PREDICATES = (r"@>", r"\?\|", r"\?&", r"(?<!\?)\?(?!\?)")
# Operator patterns that benefit from an expression index on a key.
KEY_EXTRACT_RE = re.compile(
    r"(?P<col>[a-zA-Z_][a-zA-Z0-9_]*)\s*->>?\s*'(?P<key>[^']+)'"
)

# Thresholds.
SLOW_MEAN_MS = 50.0
SLOW_TOP_N = 20


def _existing_indexes(conn: psycopg.Connection, table: str) -> list[str]:
    cur = conn.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
        (table,),
    )
    return [row[0] for row in cur.fetchall()]


def _check_extension(conn: psycopg.Connection) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements'"
    )
    return cur.fetchone() is not None


def _fetch_slow_queries(conn: psycopg.Connection) -> list[tuple[int, float, float, str]]:
    cur = conn.execute(
        "SELECT calls, mean_exec_time, total_exec_time, query "
        "FROM pg_stat_statements "
        "WHERE query NOT ILIKE 'EXPLAIN%' "
        "  AND query NOT ILIKE '%pg_stat_statements%' "
        "ORDER BY total_exec_time DESC "
        "LIMIT 500"
    )
    return cast(list[tuple[int, float, float, str]], cur.fetchall())


def _analyze_target(
    conn: psycopg.Connection,
    table: str,
    column: str,
    rows: list[tuple[int, float, float, str]]
) -> None:
    print()
    print(f"## public.{table}.{column}")
    existing = _existing_indexes(conn, table)
    relevant_existing = [d for d in existing if column in d.lower()]
    if relevant_existing:
        print(f"  Existing indexes touching {column}:")
        for d in relevant_existing:
            print(f"    - {d}")
    else:
        print(f"  No existing index touches {column}.")

    # Containment / key-existence usage
    gin_hits: list[tuple[int, float, str]] = []
    key_extract_hits: Counter[str] = Counter()
    key_extract_examples: dict[str, str] = {}

    for calls, mean_ms, _total_ms, qtext in rows:
        if column not in qtext:
            continue
        # GIN-worthy operators
        for op_pat in GIN_PREDICATES:
            if re.search(rf"{column}\s*{op_pat}", qtext):
                if float(mean_ms) >= SLOW_MEAN_MS:
                    gin_hits.append((int(calls), float(mean_ms), qtext))
                break  # one hit per query is enough
        # Expression-index opportunities
        for m in KEY_EXTRACT_RE.finditer(qtext):
            if m.group("col") != column:
                continue
            if float(mean_ms) < SLOW_MEAN_MS:
                continue
            key = m.group("key")
            key_extract_hits[key] += int(calls)
            key_extract_examples.setdefault(key, qtext)

    if gin_hits:
        print(f"  GIN candidate: queries using @> / ? on {column}")
        for calls, mean_ms, qtext in gin_hits[:3]:
            snippet = " ".join(qtext.split())[:120]
            print(
                f"    calls={calls:>6}  mean={mean_ms:>6.1f}ms  e.g. {snippet}…"
            )
        print(
            f"  --> Suggest: CREATE INDEX ON public.{table} "
            f"USING gin ({column});"
        )
    else:
        print(
            f"  No GIN-worthy traffic on {column} in last "
            f"pg_stat_statements window."
        )

    if key_extract_hits:
        print(f"  Expression-index candidates on {column}:")
        for key, n_calls in key_extract_hits.most_common(5):
            print(
                f"    key='{key}': calls={n_calls}  "
                f"--> CREATE INDEX ON public.{table} "
                f"(({column}->>'{key}'));"
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

    print("JSONB index opportunity report")
    print("==============================")

    try:
        # Confirm extension
        if not _check_extension(conn):
            print(
                "ERROR: pg_stat_statements extension is not installed.",
                file=sys.stderr,
            )
            conn.close()
            return 2

        # Pull all queries that mention any JSONB target column.
        rows = _fetch_slow_queries(conn)

        for table, column in JSONB_TARGETS:
            _analyze_target(conn, table, column, rows)

    except psycopg.Error as e:
        print(f"ERROR: pg_stat_statements query failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    print("\n(advisory only — no indexes created)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
