"""Assert hot-path queries don't fall back to Seq Scan.

For each query listed in ``HOT_PATH_QUERIES`` below:

1. ``SET LOCAL enable_seqscan = off`` — forces the planner to pick any
   usable index. On an empty table this still works: the planner only
   falls back to Seq Scan (cost = 1e10) when **no** index covers the
   query shape, regardless of row count. So this assertion is reliable
   even when the table has zero rows, which the live prod DB currently
   does for ``leads`` and ``campaign_messages``.
2. ``EXPLAIN (FORMAT JSON)`` the query.
3. Walk the plan tree; collect every ``Node Type``.
4. Fail if any node is ``Seq Scan``.

Why not ``EXPLAIN ANALYZE``? ANALYZE actually executes the query, which
would burn time / IO and (worse) on stmt-level UPDATE/DELETE would mutate
data. EXPLAIN alone returns the planner's *choice* — which is what we
want to gate on.

Run via CI or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_query_plans

Exit codes:
    0 = every hot-path query uses an Index Scan / Index Only Scan /
        Bitmap Index Scan (i.e. no Seq Scan node anywhere in the plan tree)
    1 = at least one hot-path query falls back to Seq Scan even when
        seqscan is disabled — index is missing or unusable for that
        query shape (e.g. functional-mismatch like ``lower(email)``)
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class HotPathQuery:
    label: str
    # Fully-formed ``EXPLAIN (FORMAT JSON) ...`` statement. Stored intact
    # (not assembled at runtime) so static analyzers see a constant SQL
    # body — no concatenation, no f-string interpolation.
    explain_sql: str
    # Optional params for parameterised EXPLAIN. The placeholder values
    # are dummies that only need to be type-correct so the planner picks
    # the right index.
    params: tuple[Any, ...] = ()


HOT_PATH_QUERIES: tuple[HotPathQuery, ...] = (
    HotPathQuery(
        label="leads recent (dashboard top 200)",
        explain_sql=(
            "EXPLAIN (FORMAT JSON) "
            "SELECT * FROM leads ORDER BY created_at DESC LIMIT 200"
        ),
    ),
    HotPathQuery(
        label="leads by audit_status",
        explain_sql=(
            "EXPLAIN (FORMAT JSON) "
            "SELECT * FROM leads WHERE audit_status = %s"
        ),
        params=("pending",),
    ),
    HotPathQuery(
        label="leads by unique_key (PK lookup)",
        explain_sql=(
            "EXPLAIN (FORMAT JSON) "
            "SELECT * FROM leads WHERE unique_key = %s"
        ),
        params=("X",),
    ),
    HotPathQuery(
        label="campaign_messages by campaign_id",
        explain_sql=(
            "EXPLAIN (FORMAT JSON) "
            "SELECT * FROM campaign_messages WHERE campaign_id = %s"
        ),
        params=("00000000-0000-0000-0000-000000000000",),
    ),
    HotPathQuery(
        label="leads by seo_score range (T3.1-A — /insights + UI filter)",
        explain_sql=(
            "EXPLAIN (FORMAT JSON) "
            "SELECT * FROM leads WHERE seo_score BETWEEN %s AND %s"
        ),
        params=(50, 100),
    ),
)


def _walk_node_types(plan: dict[str, Any]) -> list[str]:
    """Return every ``Node Type`` value reachable from ``plan``."""
    types: list[str] = []
    stack: list[dict[str, Any]] = [plan]
    while stack:
        node = stack.pop()
        if "Node Type" in node:
            types.append(node["Node Type"])
        for child in node.get("Plans", ()) or ():
            stack.append(child)
    return types


def _explain(conn: psycopg.Connection, query: HotPathQuery) -> list[str]:
    # nosemgrep: python.lang.security.audit.formatted-sql-query
    # query.explain_sql is a module-level constant from HOT_PATH_QUERIES
    # above (no user input, no concatenation). Parameters flow through
    # the second arg as positional binds.
    cur = conn.execute(query.explain_sql, query.params)
    row = cur.fetchone()
    assert row is not None, "EXPLAIN returned no rows"
    raw = row[0]
    # psycopg3 returns jsonb as already-parsed Python; if a driver
    # version returns a string, parse it.
    parsed = raw if isinstance(raw, list) else json.loads(raw)
    top_plan = parsed[0]["Plan"]
    return _walk_node_types(top_plan)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(url, autocommit=False)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    failures: list[str] = []
    try:
        # SET LOCAL is scoped to the current transaction. The ROLLBACK
        # in the finally below drops the setting; even an abort leaves
        # the session clean.
        conn.execute("SET LOCAL enable_seqscan = off")
        for query in HOT_PATH_QUERIES:
            node_types = _explain(conn, query)
            if "Seq Scan" in node_types:
                failures.append(
                    f"{query.label}: planner picked Seq Scan even with "
                    f"enable_seqscan=off — no usable index for query shape. "
                    f"Plan nodes: {node_types}. EXPLAIN: {query.explain_sql!r}"
                )
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error during plan probe: {e}",
              file=sys.stderr)
        return 2
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    if failures:
        print("Query plan check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("Query plan check PASSED "
          f"({len(HOT_PATH_QUERIES)} hot-path queries verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
