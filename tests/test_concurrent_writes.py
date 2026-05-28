"""Live-DB concurrency / contention tests.

Skipped automatically when ``DATABASE_URL`` is unset, so the regular
``pytest --cov`` run on a dev box does not require Supabase access.
The dedicated ``concurrency-tests`` CI job in ``ci.yml`` sets the env
and runs only this file.

Isolation strategy: every row inserted carries a ``_concurrency_test_<uuid>``
``unique_key`` prefix. The session-scoped ``_sweep_leftover_test_rows``
fixture nukes any leftovers (from a killed CI run) at the start of the
suite; per-test fixtures also clean up in teardown. Concurrent runs of
the suite (two PRs, push + cron at once) never collide because each row
carries its own UUID.

Why not wrap in a transaction-then-rollback? The whole point is testing
contention between separate database sessions — each holds its own
transaction. ROLLBACK in one session doesn't undo another. Unique-key
+ teardown cleanup is the only honest pattern here.
"""

from __future__ import annotations

import concurrent.futures
import os
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors as pg_errors  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; concurrency tests require live Supabase access",
)

# Advisory-lock namespace for lead-scoped locks. Picked arbitrarily; the
# 32-bit namespace ID must be stable across producers that want to
# serialize on the same lead — if ParallelAuditor adopts the lock, it
# uses this same constant.
LEAD_LOCK_NAMESPACE = 0x4EAD

# Valid audit_status values per the CHECK constraint
# (leads_audit_status_allowed). 4-of-8 here keeps the test focused.
VALID_AUDIT_STATUSES = ("Pending", "Processing", "Completed", "Failed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _sweep_leftover_test_rows() -> None:
    """Wipe any `_concurrency_test_*` rows left over from a killed CI run.

    A SIGKILL'd worker can't run per-test teardown — without this sweep
    those rows accumulate forever.
    """
    if not DATABASE_URL:
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM leads WHERE unique_key LIKE '\\_concurrency\\_test\\_%' ESCAPE '\\'"
        )


@pytest.fixture()
def test_lead_key() -> str:
    """Return a fresh ``_concurrency_test_<uuid>`` key; clean up in teardown."""
    key = f"_concurrency_test_{uuid.uuid4().hex}"
    yield key
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DELETE FROM leads WHERE unique_key = %s", (key,))


def _seed_lead(
    key: str,
    audit_status: str = "Pending",
    seo_score: int | None = None,
) -> None:
    """Idempotent upsert of (audit_status, seo_score). Single literal SQL —
    semgrep can verify there's no concatenation or interpolation."""
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO leads (unique_key, audit_status, seo_score) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (unique_key) DO UPDATE SET "
            "  audit_status = EXCLUDED.audit_status, "
            "  seo_score    = EXCLUDED.seo_score",
            (key, audit_status, seo_score),
        )


def _read_audit_status(key: str) -> str | None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute(
            "SELECT audit_status FROM leads WHERE unique_key = %s", (key,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def _read_seo_score(key: str) -> int | None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute("SELECT seo_score FROM leads WHERE unique_key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else None


def _row_exists(key: str) -> bool:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute("SELECT 1 FROM leads WHERE unique_key = %s", (key,))
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_concurrent_updates_converge_to_last_write_wins(test_lead_key: str) -> None:
    """20 concurrent UPDATEs to same row serialize via row-lock.

    Postgres at READ COMMITTED (Supabase default) acquires a row-level
    lock on each UPDATE — they run sequentially in some order; the last
    commit wins. The final value MUST be one of the values written, and
    every UPDATE MUST succeed (no deadlock, no `current transaction is
    aborted`).
    """
    _seed_lead(test_lead_key, audit_status="Pending")
    statuses = list(VALID_AUDIT_STATUSES) * 5  # 20 total

    def worker(i: int) -> str:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "UPDATE leads SET audit_status = %s, "
                "  updated_at = timezone('utc', now()) "
                "WHERE unique_key = %s",
                (statuses[i], test_lead_key),
            )
        return statuses[i]

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(worker, range(20), timeout=30))

    assert len(results) == 20, "not every worker returned"
    final = _read_audit_status(test_lead_key)
    assert final in VALID_AUDIT_STATUSES, (
        f"final audit_status={final!r} not in valid set — row may be in a torn state"
    )


def test_concurrent_inserts_same_unique_key(test_lead_key: str) -> None:
    """20 concurrent INSERTs with same ``unique_key``: 1 succeeds, 19 fail.

    The UNIQUE constraint on ``leads.unique_key`` (via the auto-created
    ``leads_pkey``) serializes inserts. The first to commit holds the
    key; the other 19 raise ``UniqueViolation`` at commit time.
    """

    def worker(_: int) -> str:
        try:
            with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO leads (unique_key, audit_status) "
                    "VALUES (%s, 'Pending')",
                    (test_lead_key,),
                )
            return "ok"
        except pg_errors.UniqueViolation:
            return "unique_violation"

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(worker, range(20), timeout=30))

    ok_count = results.count("ok")
    uv_count = results.count("unique_violation")
    assert ok_count == 1, f"expected exactly 1 success, got {ok_count}"
    assert uv_count == 19, f"expected 19 UniqueViolations, got {uv_count}"
    assert ok_count + uv_count == 20, (
        "got unexpected error class — check the worker's except clause"
    )


def test_concurrent_update_and_delete_converges_to_no_row(
    test_lead_key: str,
) -> None:
    """Concurrent UPDATE + DELETE: either order leaves the row deleted.

    At READ COMMITTED:
    - DELETE first → UPDATE re-evaluates WHERE, finds nothing, affects 0
      rows. No error.
    - UPDATE first → DELETE waits for the row lock, then removes the
      updated row.

    Both paths converge: row gone. No torn state, no half-applied write.
    """
    _seed_lead(test_lead_key, audit_status="Pending")

    def updater() -> None:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "UPDATE leads SET audit_status = 'Completed' WHERE unique_key = %s",
                (test_lead_key,),
            )

    def deleter() -> None:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute("DELETE FROM leads WHERE unique_key = %s", (test_lead_key,))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(updater)
        f2 = ex.submit(deleter)
        f1.result(timeout=10)
        f2.result(timeout=10)

    assert not _row_exists(test_lead_key), (
        "concurrent UPDATE + DELETE left a row — should have converged "
        "to deletion regardless of order"
    )


def test_lost_update_without_advisory_lock(test_lead_key: str) -> None:
    """Documents the Supabase-default lost-update window.

    Two writers each do SELECT → compute → UPDATE on ``seo_score``. The
    one that reads first but writes second OVERWRITES the other's value
    — even though it was already committed. This is the classic
    lost-update problem under READ COMMITTED. Supabase's default
    isolation does NOT prevent it.

    Assertion is intentionally weak: the final value must be ONE of the
    candidates. Documents that "no lost update" is NOT a guarantee from
    the database alone — application-level serialization (advisory
    lock, ``SELECT ... FOR UPDATE``, or compare-and-swap) is required if
    you need to preserve both writes.
    """
    _seed_lead(test_lead_key, audit_status="Pending", seo_score=50)

    def auditor_read_modify_write() -> int:
        with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
            cur = conn.execute(
                "SELECT seo_score FROM leads WHERE unique_key = %s",
                (test_lead_key,),
            )
            row = cur.fetchone()
            current = (row[0] if row else 0) or 0
            time.sleep(0.05)  # widen the race window
            new_score = min(current + 10, 100)  # respect the 0..100 CHECK
            conn.execute(
                "UPDATE leads SET seo_score = %s WHERE unique_key = %s",
                (new_score, test_lead_key),
            )
            conn.commit()
            return int(new_score)

    def manual_overwrite() -> int:
        time.sleep(0.02)  # land mid-window
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "UPDATE leads SET seo_score = 95 WHERE unique_key = %s",
                (test_lead_key,),
            )
        return 95

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(auditor_read_modify_write)
        f2 = ex.submit(manual_overwrite)
        v1 = f1.result(timeout=10)
        v2 = f2.result(timeout=10)

    final = _read_seo_score(test_lead_key)
    assert final in (v1, v2), (
        f"final seo_score={final!r} not from candidate writers "
        f"(auditor={v1}, manual={v2}) — torn state?"
    )


def test_advisory_lock_serializes_increments(test_lead_key: str) -> None:
    """``pg_advisory_xact_lock`` on the lead serializes read-modify-write.

    20 workers each do SELECT → +1 → UPDATE under the lock. With
    ``pg_advisory_xact_lock(NAMESPACE, hashtext(unique_key))``, only one
    worker holds the lock per (namespace, key) at a time; the others
    wait. Final value is exactly initial+20 — no lost updates.

    Without the lock the same workload loses ~half the increments at
    this contention level (verified empirically). The lock costs one
    extra round-trip per operation but is the documented fix when
    ParallelAuditor and a manual edit can race on the same lead.
    """
    _seed_lead(test_lead_key, audit_status="Pending", seo_score=0)
    increments = 20

    def worker(_: int) -> None:
        with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
            # Lock is held for the duration of the transaction; released
            # implicitly on COMMIT.
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (LEAD_LOCK_NAMESPACE, test_lead_key),
            )
            cur = conn.execute(
                "SELECT seo_score FROM leads WHERE unique_key = %s",
                (test_lead_key,),
            )
            row = cur.fetchone()
            current = (row[0] if row else 0) or 0
            # No sleep needed — lock guarantees serialization.
            conn.execute(
                "UPDATE leads SET seo_score = %s WHERE unique_key = %s",
                (current + 1, test_lead_key),
            )
            conn.commit()

    with concurrent.futures.ThreadPoolExecutor(max_workers=increments) as ex:
        list(ex.map(worker, range(increments), timeout=60))

    final = _read_seo_score(test_lead_key)
    assert final == increments, (
        f"expected initial(0)+{increments} = {increments}, got {final} "
        "— pg_advisory_xact_lock is not serializing"
    )
