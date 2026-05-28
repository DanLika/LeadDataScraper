"""NULL-ratio audit for the four core tables.

Emits a human-readable report listing, per table:

- Total row count.
- Every column with NULL ratio > ``MOSTLY_NULL_THRESHOLD`` (90% by
  default) — candidate for column-drop migration.
- Every column listed in ``APP_REQUIRED_NULLABLE`` that is still
  nullable in the schema — candidate for a NOT-NULL tightening.

Exit-1 conditions (hard invariants):

- Any NULL value in a column listed under ``MUST_NOT_BE_NULL``. These
  are columns whose presence is guaranteed by an application default
  (e.g. ``audit_status DEFAULT 'Pending'``, ``created_at DEFAULT
  timezone('utc', now())``) — a NULL here means the default failed to
  fire (a migration backfill bug, an explicit ``= NULL`` write, or a
  Studio hand-edit that bypassed the default).

Empty tables are skipped — division by zero would otherwise pin every
column at "0/0 = undefined" and every report row would be useless. The
hard-invariant counts also collapse to 0 on empty tables (a NULL count
of 0 is fine), so the gate stays silent until real data arrives.

Run via security.yml (push + daily cron) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_null_audit

Per-run cadence: daily. Per-human-review cadence: weekly — the operator
skims the latest run on Monday morning to decide which CANDIDATE DROP /
TIGHTEN items graduate into a real migration.

Exit codes:
    0 = no hard-invariant violations (report may still list findings
        for manual review)
    1 = at least one MUST_NOT_BE_NULL column has a NULL row
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)
"""

from __future__ import annotations

import os
import sys
from typing import Any

import psycopg
from psycopg import sql

TABLES: tuple[str, ...] = (
    "leads",
    "campaigns",
    "campaign_messages",
    "orchestration_jobs",
)

# Columns whose presence is enforced by a schema default + application
# invariant. Any NULL row here is a real bug — flag hard.
MUST_NOT_BE_NULL: dict[str, tuple[str, ...]] = {
    "leads": ("unique_key", "audit_status", "created_at", "updated_at"),
    "campaigns": ("name", "channel", "created_at", "updated_at"),
    "campaign_messages": ("channel", "created_at"),
    "orchestration_jobs": ("id", "status", "created_at", "updated_at"),
}

# Columns the application treats as required (the producers always
# set them, the consumers always read them) but the schema still
# permits NULL. Surface as a recommendation; not a CI failure since
# tightening a constraint is a deliberate migration, not a code-merge
# decision.
APP_REQUIRED_NULLABLE: dict[str, tuple[str, ...]] = {
    "leads": ("name", "lead_source"),
    "campaigns": ("status",),
    "campaign_messages": ("status", "campaign_id"),
    "orchestration_jobs": (),
}

MOSTLY_NULL_THRESHOLD = 0.90


def _fetch_columns(conn: psycopg.Connection, table: str) -> list[tuple[str, str, str]]:
    cur = conn.execute(
        "SELECT column_name, is_nullable, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    )
    return [(name, nullable, dtype) for name, nullable, dtype in cur.fetchall()]


def _fetch_null_counts(
    conn: psycopg.Connection, table: str, columns: list[str]
) -> dict[str, int]:
    """One pass: `COUNT(*) AS total, COUNT(*) FILTER (WHERE col IS NULL) AS "col"` per col.

    SQL is composed via ``psycopg.sql.Identifier`` so column + table
    names are escaped as identifiers — there is no string concatenation
    of user-controllable values. Table/column names come from
    ``information_schema``, not from any HTTP / file input.
    """
    select_parts: list[sql.Composable] = [sql.SQL("COUNT(*) AS total")]
    for col in columns:
        select_parts.append(
            sql.SQL("COUNT(*) FILTER (WHERE {c} IS NULL) AS {a}").format(
                c=sql.Identifier(col),
                a=sql.Identifier(col),
            )
        )
    stmt = sql.SQL("SELECT {fields} FROM {tbl}").format(
        fields=sql.SQL(", ").join(select_parts),
        tbl=sql.Identifier("public", table),
    )
    # `stmt` is a psycopg.sql.Composed value built ONLY from
    # sql.Identifier() (auto-escaped) and constant sql.SQL() fragments.
    # Table + column names come from information_schema in
    # _fetch_columns(), not user input. Canonical safe dynamic-SQL
    # pattern in psycopg3.
    cur = conn.execute(stmt)  # nosemgrep
    row = cur.fetchone()
    assert row is not None, f"empty aggregate result on {table}"
    headers = [d.name for d in cur.description or []]
    return dict(zip(headers, row))


def _audit_table(conn: psycopg.Connection, table: str) -> tuple[list[str], list[str]]:
    """Return (report_lines, failure_lines) for one table."""
    report: list[str] = []
    failures: list[str] = []

    columns = _fetch_columns(conn, table)
    if not columns:
        report.append(
            f"## {table}\n  TABLE MISSING — drift detector should also catch this"
        )
        return report, failures

    col_names = [c[0] for c in columns]
    nullable_by_col = {name: nullable for name, nullable, _ in columns}
    null_map = _fetch_null_counts(conn, table, col_names)
    total = int(null_map["total"])

    report.append(f"## {table}  (rows: {total})")
    if total == 0:
        report.append("  (empty — skipping NULL-ratio analysis)")
        # No need to scan MUST_NOT_BE_NULL: every null count is 0.
        return report, failures

    # Hard invariants
    for col in MUST_NOT_BE_NULL.get(table, ()):
        if col in null_map and int(null_map[col]) > 0:
            failures.append(
                f"{table}.{col}: {null_map[col]} NULL row(s) "
                f"— violates MUST_NOT_BE_NULL invariant (default + app guarantee)"
            )

    # Mostly-NULL candidates for drop
    drop_candidates: list[str] = []
    for name, n_null in null_map.items():
        if name == "total":
            continue
        ratio = int(n_null) / total
        if ratio > MOSTLY_NULL_THRESHOLD:
            drop_candidates.append(
                f"  CANDIDATE_DROP: {name}: {ratio:.1%} NULL ({n_null}/{total})"
            )
    if drop_candidates:
        report.append("  --- columns with >90% NULL (consider dropping) ---")
        report.extend(sorted(drop_candidates))

    # App-required-but-nullable
    tighten: list[str] = []
    for col in APP_REQUIRED_NULLABLE.get(table, ()):
        if nullable_by_col.get(col) == "YES":
            n_null = int(null_map.get(col, 0))
            ratio = n_null / total
            tighten.append(
                f"  TIGHTEN: {col} is app-required but nullable in schema "
                f"(currently {n_null}/{total} = {ratio:.1%} NULL)"
            )
    if tighten:
        report.append("  --- columns to consider NOT NULL ---")
        report.extend(tighten)

    return report, failures


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

    all_report: list[str] = ["NULL Audit Report", "================="]
    all_failures: list[str] = []

    try:
        for table in TABLES:
            report, failures = _audit_table(conn, table)
            all_report.extend(["", *report])
            all_failures.extend(failures)
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error during NULL probe: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print("\n".join(all_report))

    if all_failures:
        print("\nHARD INVARIANT VIOLATIONS:", file=sys.stderr)
        for line in all_failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("\n(no hard-invariant violations — report items are advisory)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
