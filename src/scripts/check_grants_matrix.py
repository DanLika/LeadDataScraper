"""Full GRANTS matrix audit for public-schema tables + role enumeration.

Beyond the deny-all RLS posture asserted by ``schema_drift_check.py``,
this gate enumerates **every** ``information_schema.table_privileges``
row and asserts:

- ``anon`` has ZERO grants on the 7 core tables.
- ``authenticated`` has ZERO grants on the 7 core tables.
- ``PUBLIC`` has ZERO grants on the 7 core tables.
- ``service_role`` has the full DML+DDL set (the backend is the only
  legitimate writer; missing privs would break the orchestrator /
  dispatcher).
- ``postgres`` has the full set (DB owner; always allowed).
- No **other** role appears in the grants matrix for these 7 tables.

Also enumerates ``pg_roles`` and flags any role not in
``EXPECTED_ROLES`` — catches Studio-created roles, accidental
``CREATE ROLE`` via an extension, or compromise.

Run via security.yml or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_grants_matrix

Exit codes:
    0 = matrix matches expectation
    1 = drift detected
    2 = misconfigured run
"""
from __future__ import annotations

import os
import sys

import psycopg

TABLES: tuple[str, ...] = (
    "leads", "campaigns", "campaign_messages", "orchestration_jobs",
    "account_deletions",
    "email_send_ledger", "suppressions",
)
TABLE_LIST = list(TABLES)

# Full DML+DDL privilege set Postgres exposes via information_schema.
FULL_PRIVILEGES: frozenset[str] = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "REFERENCES", "TRIGGER", "TRUNCATE",
})

# Roles that MUST have the full privilege set on every core table.
FULL_PRIVILEGE_ROLES: frozenset[str] = frozenset({"postgres", "service_role"})

# Roles that MUST have ZERO grants on the core tables.
ZERO_PRIVILEGE_ROLES: frozenset[str] = frozenset({
    "anon", "authenticated", "PUBLIC",
})

# Every other role appearing in the matrix is unexpected.
ALLOWED_GRANTEES: frozenset[str] = FULL_PRIVILEGE_ROLES | ZERO_PRIVILEGE_ROLES

# Roles expected to exist in pg_roles. Anything outside this set is a
# Studio-created role, an extension-installed role, or worse — flag it.
EXPECTED_ROLES: frozenset[str] = frozenset({
    # Supabase application roles
    "anon", "authenticated", "service_role", "authenticator",
    "dashboard_user", "pgbouncer", "postgres",
    "supabase_admin", "supabase_auth_admin", "supabase_etl_admin",
    "supabase_privileged_role", "supabase_read_only_user",
    "supabase_realtime_admin", "supabase_replication_admin",
    "supabase_storage_admin",
    # Postgres built-in pg_* roles
    "pg_checkpoint", "pg_create_subscription", "pg_database_owner",
    "pg_execute_server_program", "pg_maintain", "pg_monitor",
    "pg_read_all_data", "pg_read_all_settings", "pg_read_all_stats",
    "pg_read_server_files", "pg_signal_backend", "pg_stat_scan_tables",
    "pg_use_reserved_connections", "pg_write_all_data",
    "pg_write_server_files",
})


def _fetch_grants(conn: psycopg.Connection) -> dict[tuple[str, str], set[str]]:
    """Return mapping ``(table, grantee) → set(privilege_type)``."""
    cur = conn.execute(
        "SELECT table_name, grantee, privilege_type "
        "FROM information_schema.table_privileges "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (TABLE_LIST,),
    )
    out: dict[tuple[str, str], set[str]] = {}
    for table, grantee, priv in cur.fetchall():
        out.setdefault((table, grantee), set()).add(priv)
    return out


def _check_matrix(matrix: dict[tuple[str, str], set[str]]) -> list[str]:
    errs: list[str] = []
    seen_grantees = {grantee for _, grantee in matrix.keys()}

    # Full-privilege roles
    for table in TABLES:
        for role in FULL_PRIVILEGE_ROLES:
            privs = matrix.get((table, role), set())
            missing = FULL_PRIVILEGES - privs
            if missing:
                errs.append(
                    f"public.{table}: role {role!r} missing privileges "
                    f"{sorted(missing)} (expected the full set)"
                )

    # Zero-privilege roles
    for table in TABLES:
        for role in ZERO_PRIVILEGE_ROLES:
            privs = matrix.get((table, role), set())
            if privs:
                errs.append(
                    f"public.{table}: role {role!r} has unexpected grants "
                    f"{sorted(privs)} (expected zero)"
                )

    # Unexpected grantees
    for grantee in sorted(seen_grantees - ALLOWED_GRANTEES):
        affected = sorted(
            t for (t, g) in matrix.keys() if g == grantee
        )
        errs.append(
            f"unexpected grantee {grantee!r} appears in public-table "
            f"grants matrix (tables: {affected})"
        )
    return errs


def _check_role_enumeration(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute("SELECT rolname FROM pg_roles")
    found = {row[0] for row in cur.fetchall()}
    unexpected = sorted(found - EXPECTED_ROLES)
    missing = sorted(EXPECTED_ROLES - found)
    errs: list[str] = []
    if unexpected:
        errs.append(
            f"unexpected role(s) present in pg_roles: {unexpected} "
            f"(extension install? Studio CREATE ROLE? compromise?)"
        )
    if missing:
        errs.append(
            f"expected role(s) missing from pg_roles: {missing} "
            f"(Supabase platform regression?)"
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
        matrix = _fetch_grants(conn)
        failures.extend(_check_matrix(matrix))
        failures.extend(_check_role_enumeration(conn))
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if failures:
        print("Grants matrix check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(
        f"Grants matrix check PASSED "
        f"({len(TABLES)} tables × {len(ALLOWED_GRANTEES)} expected grantees verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
