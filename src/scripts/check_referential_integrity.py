"""Live referential-integrity check for the Supabase schema.

Exercises two invariants the static schema-drift check cannot verify:

1. CASCADE delete: deleting a row in ``campaigns`` removes its
   ``campaign_messages`` children (declared as
   ``REFERENCES campaigns(id) ON DELETE CASCADE``).
2. FK enforcement on ``campaign_messages.lead_unique_key`` — inserting a
   row referencing a non-existent ``leads.unique_key`` must raise
   ``ForeignKeyViolation``.

Run via CI (see schema-drift workflow steps) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_referential_integrity

Exit codes:
    0 = both invariants hold
    1 = at least one invariant is broken
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)

Safety: every INSERT/DELETE runs inside a single transaction that is
**unconditionally rolled back** in a ``finally`` block. Postgres' default
READ COMMITTED isolation hides the in-flight rows from other transactions,
and the ROLLBACK undoes them entirely. Even a network drop mid-test leaves
the DB clean — Postgres rolls back any open transaction when the backend
session ends. UUIDs prevent collisions between concurrent CI runs.
"""
from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg import errors as pg_errors


def _scalar(cur: psycopg.Cursor) -> int:
    row = cur.fetchone()
    assert row is not None, "expected a single row from COUNT(*) query"
    return int(row[0])


def _run_cascade_test(conn: psycopg.Connection) -> list[str]:
    failures: list[str] = []
    campaign_id = uuid.uuid4()
    test_name = f"_integrity_test_{campaign_id}"

    conn.execute(
        "INSERT INTO campaigns (id, name, channel) VALUES (%s, %s, %s)",
        (campaign_id, test_name, "email"),
    )
    for _ in range(5):
        conn.execute(
            "INSERT INTO campaign_messages (campaign_id, channel) "
            "VALUES (%s, %s)",
            (campaign_id, "email"),
        )

    cur = conn.execute(
        "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = %s",
        (campaign_id,),
    )
    n_before = _scalar(cur)
    if n_before != 5:
        failures.append(
            f"setup invariant broken: expected 5 child rows before delete, "
            f"got {n_before} (campaign_messages insert path may be broken)"
        )

    conn.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))

    cur = conn.execute(
        "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = %s",
        (campaign_id,),
    )
    n_after = _scalar(cur)
    if n_after != 0:
        failures.append(
            f"CASCADE delete broken: {n_after} orphan campaign_messages "
            f"survived parent delete (FK should be "
            f"campaign_messages.campaign_id REFERENCES campaigns(id) "
            f"ON DELETE CASCADE)"
        )
    return failures


def _run_fk_violation_test(conn: psycopg.Connection) -> list[str]:
    failures: list[str] = []
    campaign_id = uuid.uuid4()
    bogus_lead_key = f"_nonexistent_{uuid.uuid4()}"

    conn.execute(
        "INSERT INTO campaigns (id, name, channel) VALUES (%s, %s, %s)",
        (campaign_id, f"_integrity_test_{campaign_id}", "email"),
    )

    # Savepoint: the bogus INSERT raises ForeignKeyViolation, which
    # aborts only the inner transaction block — the outer txn (and its
    # rollback) survive.
    fk_violation_raised = False
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO campaign_messages "
                "(campaign_id, lead_unique_key, channel) "
                "VALUES (%s, %s, %s)",
                (campaign_id, bogus_lead_key, "email"),
            )
    except pg_errors.ForeignKeyViolation:
        fk_violation_raised = True

    if not fk_violation_raised:
        failures.append(
            "FK constraint not enforced: INSERT into campaign_messages "
            f"with non-existent lead_unique_key={bogus_lead_key!r} "
            "succeeded — expected ForeignKeyViolation. The FK "
            "campaign_messages.lead_unique_key → leads.unique_key is "
            "missing or DEFERRED."
        )
    return failures


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
        failures += _run_cascade_test(conn)
        failures += _run_fk_violation_test(conn)
    except psycopg.Error as e:
        # Unexpected DB error — surface it as a misconfiguration rather
        # than a silent pass. NOT a regular failure since the rollback
        # below still cleans up.
        print(f"ERROR: unexpected DB error during integrity probe: {e}",
              file=sys.stderr)
        return 2
    finally:
        # ALWAYS roll back — never leave _integrity_test_* rows on prod.
        try:
            conn.rollback()
        finally:
            conn.close()

    if failures:
        print("Referential integrity FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("Referential integrity PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
