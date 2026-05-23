"""Audit every function in the ``public`` schema for safety invariants.

Asserts (per ``pg_proc`` + ``information_schema.role_routine_grants``):

- Only functions in ``EXPECTED_FUNCTIONS`` exist — anything else is a
  Studio-added function or extension surprise; review needed.
- Every SECURITY DEFINER function:
  - is owned by ``postgres`` (no demoted ownership that would expose
    the SECDEF authority via a less-trusted role's ALTER FUNCTION),
  - has ``search_path`` set in ``proconfig`` (else built-in identifier
    resolution can be hijacked by a shadowing object in ``public``),
  - has no EXECUTE grant to ``anon``, ``authenticated``, or ``PUBLIC``
    unless explicitly allowlisted in ``EXEC_GRANT_ALLOWLIST``.

Run via security.yml or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_function_safety

Exit codes: 0 OK / 1 drift / 2 misconfig.
"""
from __future__ import annotations

import os
import sys

import psycopg

# Allowlist of public-schema functions we declare. Update in lock-step
# with supabase_schema.sql (add_lead_column) and any Supabase-managed
# helpers (rls_auto_enable, update_updated_at_column are platform
# triggers Supabase ships when RLS auto-enable is configured).
EXPECTED_FUNCTIONS: frozenset[str] = frozenset({
    "add_lead_column",
    "rls_auto_enable",
    "update_updated_at_column",
})

# EXECUTE-grant exceptions: functions allowed to be callable by
# untrusted roles. Empty by default — every public function should be
# behind the service_role boundary unless explicitly intended for
# anon/authenticated.
EXEC_GRANT_ALLOWLIST: dict[str, frozenset[str]] = {}

UNTRUSTED_GRANTEES: frozenset[str] = frozenset({
    "anon", "authenticated", "PUBLIC",
})


def _check_function_set(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT p.proname "
        "FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = 'public'"
    )
    found = {row[0] for row in cur.fetchall()}
    errs: list[str] = []
    unexpected = sorted(found - EXPECTED_FUNCTIONS)
    if unexpected:
        errs.append(
            f"unexpected function(s) in public schema: {unexpected} "
            f"(add to EXPECTED_FUNCTIONS allowlist or DROP them)"
        )
    missing = sorted(EXPECTED_FUNCTIONS - found)
    if missing:
        errs.append(
            f"declared function(s) missing from DB: {missing}"
        )
    return errs


def _check_secdef_safety(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = 'public'"
    )
    errs: list[str] = []
    for proname, secdef, owner, cfg in cur.fetchall():
        if not secdef:
            continue
        if owner != "postgres":
            errs.append(
                f"public.{proname} is SECURITY DEFINER but owner={owner!r} "
                f"(expected 'postgres' — anyone with that role can ALTER "
                f"FUNCTION and elevate)"
            )
        if not any(
            (s or "").lower().startswith("search_path=") for s in (cfg or [])
        ):
            errs.append(
                f"public.{proname} is SECURITY DEFINER but has no "
                f"search_path set — built-in identifier resolution is "
                f"hijackable via a shadowing object in public"
            )
    return errs


def _check_execute_grants(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT routine_name, grantee, privilege_type "
        "FROM information_schema.role_routine_grants "
        "WHERE routine_schema = 'public' "
        "  AND grantee = ANY(%s)",
        (list(UNTRUSTED_GRANTEES),),
    )
    errs: list[str] = []
    for routine, grantee, priv in cur.fetchall():
        allowed = EXEC_GRANT_ALLOWLIST.get(routine, frozenset())
        if grantee not in allowed:
            errs.append(
                f"public.{routine} has {priv} grant to {grantee} "
                f"— not in EXEC_GRANT_ALLOWLIST. REVOKE EXECUTE ON "
                f"FUNCTION public.{routine} FROM {grantee};"
            )
    return errs


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
    try:
        failures.extend(_check_function_set(conn))
        failures.extend(_check_secdef_safety(conn))
        failures.extend(_check_execute_grants(conn))
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if failures:
        print("Function safety check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(
        f"Function safety check PASSED "
        f"({len(EXPECTED_FUNCTIONS)} functions, SECDEF safety verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
