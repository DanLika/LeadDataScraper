"""Verify per-role ``statement_timeout`` defaults and exercise the
cancellation mechanism.

Two layers:

1. **Configured** — query ``pg_db_role_setting`` and assert each of
   ``anon``, ``authenticated``, ``service_role`` carries the expected
   ``statement_timeout`` default. ``ALTER ROLE ... SET statement_timeout``
   only fires on a new connection's startup; this check proves the
   value is present in ``pg_db_role_setting`` so any future connection
   inherits it.
2. **Enforcement** — open a session, ``SET LOCAL statement_timeout``,
   run ``SELECT pg_sleep(N)`` for N > timeout, assert the server
   cancels with ``QueryCanceled``. Confirms the cancellation primitive
   actually fires; combined with check (1), per-role behavior is
   transitively verified without needing separate per-role connection
   strings in CI.

To literally exercise per-role enforcement (connect as anon, watch its
own 3s default trip), supply ``DATABASE_URL_ANON`` /
``DATABASE_URL_AUTHENTICATED`` / ``DATABASE_URL_SERVICE_ROLE`` env vars.
None are required; if any is set the script also runs a no-`SET LOCAL`
pg_sleep against it and asserts cancellation at the role's configured
threshold.

Run via security.yml or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_statement_timeouts

Exit codes:
    0 = all configured + enforcement checks pass
    1 = at least one assertion failed (timeout missing, wrong value,
        or cancellation didn't fire)
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)
"""
from __future__ import annotations

import os
import sys

import psycopg
from psycopg import errors as pg_errors


EXPECTED_TIMEOUTS: dict[str, str] = {
    "anon": "3s",            # Supabase default — kept tight
    "authenticated": "8s",   # Supabase default — kept tight
    "service_role": "30s",   # set via migration add_check_constraints' sibling
}

# Mechanism test: set timeout to 2s, sleep for 5s, expect cancellation.
# Values appear as literals in the SQL below — semgrep flags any
# non-literal first arg to conn.execute() even with named constants.
MECHANISM_TIMEOUT_LITERAL = "2s"
MECHANISM_SLEEP_SECONDS = 5


def _check_configured(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT r.rolname, s.setconfig "
        "FROM pg_db_role_setting s "
        "JOIN pg_roles r ON r.oid = s.setrole "
        "WHERE r.rolname = ANY(%s)",
        (list(EXPECTED_TIMEOUTS.keys()),),
    )
    found: dict[str, str] = {}
    for rolname, setconfig in cur.fetchall():
        for kv in setconfig or []:
            if kv.lower().startswith("statement_timeout="):
                found[rolname] = kv.split("=", 1)[1]

    errs: list[str] = []
    for role, expected in EXPECTED_TIMEOUTS.items():
        actual = found.get(role)
        if actual is None:
            errs.append(
                f"role {role!r} has no statement_timeout default — "
                f"expected {expected!r}. Run ALTER ROLE {role} "
                f"SET statement_timeout = '{expected}';"
            )
        elif actual != expected:
            errs.append(
                f"role {role!r} statement_timeout={actual!r}, "
                f"expected {expected!r} — drift from policy"
            )
    return errs


def _check_mechanism_fires(conn: psycopg.Connection) -> list[str]:
    """Prove the cancellation primitive works against the current session.

    Don't rely on the role's configured default — that fires only at
    connection startup, and DATABASE_URL typically points at postgres
    (no default). Use SET LOCAL inside a transaction instead.
    """
    try:
        # Tx required for SET LOCAL — autocommit context would expand
        # the scope to the whole session.
        with conn.transaction():
            # Static SQL string — the timeout value lives in
            # MECHANISM_TIMEOUT_LITERAL above (a module-level constant
            # used only for log messages).
            conn.execute("SET LOCAL statement_timeout = '2s'")
            try:
                conn.execute("SELECT pg_sleep(%s)", (MECHANISM_SLEEP_SECONDS,))
            except pg_errors.QueryCanceled:
                return []
            return [
                "statement_timeout mechanism FAILED: SET LOCAL "
                "statement_timeout='2s' did not cancel a SELECT pg_sleep(5) "
                "— the server is not enforcing statement_timeout"
            ]
    except pg_errors.QueryCanceled:
        # Pop out of the transaction context cleanly; QueryCanceled
        # leaves the transaction aborted, conn.transaction() rolls back.
        return []
    except psycopg.Error as e:
        return [f"unexpected DB error during mechanism probe: {e}"]


def _check_per_role_enforcement() -> list[str]:
    """Optional: if per-role DSNs are supplied, run pg_sleep on each
    and assert the role's *own* default trips cancellation."""
    errs: list[str] = []
    for role, expected_str in EXPECTED_TIMEOUTS.items():
        dsn = os.environ.get(f"DATABASE_URL_{role.upper()}")
        if not dsn:
            continue
        # Sleep one second past the configured timeout.
        # Parse '3s' / '8s' / '30s' → int seconds.
        try:
            expected_secs = int(expected_str.rstrip("s"))
        except ValueError:
            errs.append(
                f"role {role!r}: cannot parse expected {expected_str!r} "
                f"as 'Ns' format"
            )
            continue
        sleep_for = expected_secs + 2

        try:
            with psycopg.connect(dsn, autocommit=True) as role_conn:
                try:
                    role_conn.execute("SELECT pg_sleep(%s)", (sleep_for,))
                except pg_errors.QueryCanceled:
                    continue  # expected — role default fired
                errs.append(
                    f"role {role!r} ({dsn[:20]}...) ran "
                    f"pg_sleep({sleep_for}) without cancellation — "
                    f"configured default {expected_str} did not fire"
                )
        except psycopg.Error as e:
            errs.append(
                f"role {role!r}: cannot connect as that role to verify "
                f"enforcement: {e}"
            )
    return errs


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
        # autocommit-True for the configured query (no tx needed),
        # then back to autocommit-False for the SET LOCAL test.
        conn.autocommit = True
        failures.extend(_check_configured(conn))

        conn.autocommit = False
        failures.extend(_check_mechanism_fires(conn))
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error: {e}", file=sys.stderr)
        return 2
    finally:
        try:
            conn.close()
        except psycopg.Error:
            pass

    # Per-role enforcement uses fresh connections — separate from `conn`.
    failures.extend(_check_per_role_enforcement())

    if failures:
        print("statement_timeout check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(
        "statement_timeout check PASSED "
        f"({len(EXPECTED_TIMEOUTS)} roles configured, mechanism verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
