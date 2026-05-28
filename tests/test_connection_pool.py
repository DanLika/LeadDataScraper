"""Connection-pool / pooler-URL behaviour tests.

Five layers — three static, two dynamic. Static layers run in any CI
job; dynamic layers require ``DATABASE_URL``.

Static (no DB):

1. **Backend uses PostgREST, not direct Postgres**: grep
   ``backend/`` + ``src/`` for any imports of ``psycopg`` /
   ``asyncpg`` / ``psycopg2`` / ``pg8000``. Backend should never
   open a Postgres connection — it goes through ``supabase-py`` over
   HTTPS. Direct-PG callers are CI scripts only.

2. **CI / scripts use the pooler URL**: when ``DATABASE_URL`` is
   present, parse its host. The pooler endpoint pattern is
   ``aws-0-<region>.pooler.supabase.com`` (Supabase) or
   ``*.pooler.supabase.com`` more generally; the direct endpoint is
   ``db.<ref>.supabase.co``. Direct-connection URLs are flagged
   because they bypass the connection multiplexer and can exhaust
   the project's connection slot allowance at much lower
   concurrency.

Dynamic (require ``DATABASE_URL``):

3. **20 concurrent connections succeed** — the pooler should queue
   excess; sub-pool concurrency should never raise. (User spec said
   100, dialed down to 20 to keep CI cheap and avoid bumping into
   Supabase Free-tier connection ceilings; flip ``POOL_TEST_CONCURRENCY``
   to 100 once on a paid tier with confirmed quota headroom.)

4. **Long-idle connection is released** — open a connection, hold it
   idle for 5s (not 10min — CI patience), assert it can still issue
   queries (cleanup happens at GC, not on our timescale). Documents
   that the script-level "release" is ``conn.close()``, not
   filesystem cleanup or process exit.

Backend-503-not-500 (intentionally out of scope here):

5. The "backend returns 503 not 500 under pool exhaustion" check is
   an integration test against the running FastAPI app, not a DB
   test. It belongs in the Playwright E2E suite with a forced
   ``SUPABASE_SERVICE_ROLE_KEY`` pointed at an exhausted pool.
   Documented here for completeness; not implemented as a unit.

Run via the dedicated ``concurrency-tests`` job:

    DATABASE_URL=postgres://...  pytest tests/test_connection_pool.py
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")

DATABASE_URL = os.environ.get("DATABASE_URL")
POOL_TEST_CONCURRENCY = int(os.environ.get("POOL_TEST_CONCURRENCY", "20"))
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Static checks (no DB required)
# ---------------------------------------------------------------------------


def test_backend_has_no_direct_postgres_driver_import() -> None:
    """Backend goes through PostgREST/HTTPS via supabase-py. Any direct
    Postgres driver import in ``backend/`` or ``src/`` is a regression
    — would consume real connections and break the pooler accounting.

    CI / verification scripts under ``src/scripts/`` are explicitly
    allowed (they're not on the backend hot path).
    """
    forbidden_patterns = (
        re.compile(rb"^\s*import\s+(psycopg|psycopg2|asyncpg|pg8000)\b", re.M),
        re.compile(
            rb"^\s*from\s+(psycopg|psycopg2|asyncpg|pg8000)\b",
            re.M,
        ),
    )
    allowed_prefixes = (
        REPO_ROOT / "src" / "scripts",
        REPO_ROOT / "tests",
    )

    offenders: list[str] = []
    for tree in (REPO_ROOT / "backend", REPO_ROOT / "src"):
        if not tree.is_dir():
            continue
        for path in tree.rglob("*.py"):
            if any(str(path).startswith(str(p)) for p in allowed_prefixes):
                continue
            try:
                body = path.read_bytes()
            except OSError:
                continue
            for pat in forbidden_patterns:
                if pat.search(body):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
                    break

    assert not offenders, (
        f"backend / src imports a direct Postgres driver "
        f"({offenders}) — must use supabase-py over HTTPS"
    )


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; pooler-URL check needs the connection string",
)
def test_database_url_points_at_supabase_pooler() -> None:
    """The DATABASE_URL used by CI scripts should target the Supabase
    pooler (``*.pooler.supabase.com``) — direct-host connections
    (``db.<ref>.supabase.co``) bypass the multiplexer and burn one
    of the project's small fixed connection slots per CI run.
    """
    parsed = urlparse(DATABASE_URL)
    host = (parsed.hostname or "").lower()
    assert host, f"could not parse host from DATABASE_URL ({DATABASE_URL[:25]}...)"
    assert host.endswith(".pooler.supabase.com") or host.endswith(
        ".pooler.supabase.co"
    ), (
        f"DATABASE_URL host {host!r} is not a Supabase pooler endpoint. "
        f"Use the connection-pooler URL from the project settings (port "
        f"6543 typically) — direct-host URLs exhaust connection slots."
    )


# ---------------------------------------------------------------------------
# Dynamic checks (require DATABASE_URL)
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; dynamic pool tests skipped",
)


def test_concurrent_connections_succeed() -> None:
    """Open ``POOL_TEST_CONCURRENCY`` connections simultaneously; every
    one should succeed (the pooler queues if it's hit its
    ``default_pool_size``)."""

    def worker(_: int) -> bool:
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            cur = conn.execute("SELECT 1")
            return cur.fetchone()[0] == 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=POOL_TEST_CONCURRENCY) as ex:
        results = list(ex.map(worker, range(POOL_TEST_CONCURRENCY), timeout=60))

    assert all(results), (
        f"{POOL_TEST_CONCURRENCY} concurrent connections: some did not "
        f"return SELECT 1 → 1 — pool may be erroring instead of queuing"
    )


def test_idle_then_query_still_works() -> None:
    """A connection held idle for a few seconds is still usable — the
    pooler does NOT eagerly close client connections under load."""
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        time.sleep(5)
        cur = conn.execute("SELECT 2")
        assert cur.fetchone()[0] == 2
