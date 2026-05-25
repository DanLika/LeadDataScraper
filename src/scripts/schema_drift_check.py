"""Verify Supabase DB matches supabase_schema.sql + RLS posture.

Run via CI (see ``.github/workflows/ci.yml`` schema-drift job) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.schema_drift_check

Exit codes:
    0 = all assertions pass
    1 = one or more assertions fail (drift found)
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)

Assertions:
- Every column declared in ``supabase_schema.sql`` exists in DB.
- Every column in DB is declared in ``supabase_schema.sql`` (no silent drift).
- RLS enabled on leads, campaigns, campaign_messages, orchestration_jobs,
  account_deletions, email_send_ledger, email_suppression.
- A deny-all policy (AS RESTRICTIVE, qual=false, with_check=false,
  anon+authenticated, FOR ALL) exists on each of those 7 tables. RESTRICTIVE
  is enforced so a future ad-hoc PERMISSIVE qual=true policy added in Studio
  cannot OR over the deny.
- No GRANT to anon / authenticated / PUBLIC on those 7 tables.
- ``add_lead_column`` function is ``SECURITY DEFINER``, owned by ``postgres``,
  with ``search_path`` set, and has no EXECUTE grant to anon/authenticated/PUBLIC.

The column check is **name-only** by design — type drift is out of scope here
because Supabase's "live state" already drifts on a couple of columns
(``needs_manual_review`` text vs boolean, ``outreach_score`` double vs int).
A future type-parity gate can be added once those are reconciled.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO_ROOT / "supabase_schema.sql"
TABLES: tuple[str, ...] = (
    "leads",
    "campaigns",
    "campaign_messages",
    "orchestration_jobs",
    "account_deletions",
    "email_send_ledger",
    "email_suppression",
)
TABLE_CONSTRAINT_KEYWORDS = {
    "CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE", "LIKE",
}

# Named CHECK constraints declared in supabase_schema.sql. Drift in either
# direction (DB missing one, or DB carrying an undeclared one) is flagged.
# Add new constraints to the schema file in a DO $$ ... duplicate_object
# block and append the name here in the same PR.
EXPECTED_CHECK_CONSTRAINTS: dict[str, set[str]] = {
    "leads": {
        "leads_seo_score_range",
        "leads_outreach_score_range",
        "leads_audit_status_allowed",
        "leads_enrichment_status_allowed",
        "leads_email_basic_shape",
    },
    "orchestration_jobs": {
        "orchestration_jobs_status_allowed",
    },
    "campaigns": {
        "campaigns_channel_allowed",
        "campaigns_status_allowed",
    },
    "campaign_messages": {
        "campaign_messages_channel_allowed",
        "campaign_messages_status_allowed",
    },
    "email_suppression": {
        "email_suppression_reason_allowed",
        "email_suppression_source_allowed",
    },
    "email_send_ledger": {
        "email_send_ledger_provider_allowed",
    },
}


def _strip_line_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _split_top_level_commas(body: str) -> list[str]:
    """Split on commas that sit at paren-depth 0."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _find_matching_paren(sql: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parens in schema file")


def parse_expected_columns(path: Path) -> dict[str, set[str]]:
    sql = _strip_line_comments(path.read_text())
    expected: dict[str, set[str]] = {t: set() for t in TABLES}

    create_pat = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?(\w+)\s*\(",
        re.IGNORECASE,
    )
    for m in create_pat.finditer(sql):
        table = m.group(1)
        if table not in expected:
            continue
        open_paren = m.end() - 1
        close_paren = _find_matching_paren(sql, open_paren)
        body = sql[open_paren + 1 : close_paren]
        for col_def in _split_top_level_commas(body):
            tokens = col_def.split()
            if not tokens:
                continue
            head = tokens[0].strip('"').upper()
            if head in TABLE_CONSTRAINT_KEYWORDS:
                continue
            expected[table].add(tokens[0].strip('"').lower())

    # String-strip before scanning ALTER TABLE so a plpgsql `EXECUTE
    # format('ALTER TABLE ... ADD COLUMN IF NOT EXISTS %I ...', col)` body
    # (inside add_lead_column) is not parsed as a real DDL statement.
    alter_pat = re.compile(
        r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+ADD\s+COLUMN\s+"
        r"(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        re.IGNORECASE,
    )
    for table, col in alter_pat.findall(_strip_string_literals(sql)):
        if table in expected:
            expected[table].add(col.lower())

    return expected


def fetch_db_columns(conn: psycopg.Connection) -> dict[str, set[str]]:
    actual: dict[str, set[str]] = {t: set() for t in TABLES}
    cur = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (list(TABLES),),
    )
    for table, col in cur.fetchall():
        actual[table].add(col.lower())
    return actual


def check_rls(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT c.relname, c.relrowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        (list(TABLES),),
    )
    rows = {name: enabled for name, enabled in cur.fetchall()}
    errs: list[str] = []
    for t in TABLES:
        if t not in rows:
            errs.append(f"table public.{t} missing entirely")
        elif not rows[t]:
            errs.append(f"RLS disabled on public.{t}")
    return errs


def check_deny_policies(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (list(TABLES),),
    )
    by_table: dict[str, list[tuple]] = {}
    for table, name, permissive, roles, cmd, qual, with_check in cur.fetchall():
        by_table.setdefault(table, []).append(
            (name, permissive, set(roles or []), cmd, qual, with_check)
        )

    errs: list[str] = []
    for t in TABLES:
        ok = False
        for name, permissive, roles, cmd, qual, with_check in by_table.get(t, []):
            if (
                name == f"{t}_deny_all"
                and permissive == "RESTRICTIVE"
                and {"anon", "authenticated"}.issubset(roles)
                and cmd == "ALL"
                and qual == "false"
                and with_check == "false"
            ):
                ok = True
                break
        if not ok:
            errs.append(
                f"deny_all policy missing or misconfigured on public.{t} "
                f"(expected name={t}_deny_all, permissive=RESTRICTIVE, "
                f"roles>={{anon,authenticated}}, FOR ALL, qual=false, "
                f"with_check=false)"
            )
    return errs


def check_table_grants(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT table_name, grantee, privilege_type "
        "FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name = ANY(%s) "
        "AND grantee IN ('anon', 'authenticated', 'PUBLIC')",
        (list(TABLES),),
    )
    return [
        f"public.{t} has {priv} grant to {grantee} "
        f"(must be revoked — backend uses service_role only)"
        for t, grantee, priv in cur.fetchall()
    ]


def check_check_constraints(conn: psycopg.Connection) -> list[str]:
    """Assert every named CHECK constraint declared in supabase_schema.sql
    is present in the live DB, and that no extra ones have crept in.

    Extra constraints aren't a security risk on their own, but they
    signal an undocumented Supabase Studio edit that should be either
    promoted into the schema file or reverted.
    """
    cur = conn.execute(
        "SELECT t.relname, c.conname "
        "FROM pg_constraint c "
        "JOIN pg_class t ON c.conrelid = t.oid "
        "JOIN pg_namespace n ON t.relnamespace = n.oid "
        "WHERE n.nspname = 'public' "
        "  AND t.relname = ANY(%s) "
        "  AND c.contype = 'c'",
        (list(TABLES),),
    )
    actual: dict[str, set[str]] = {t: set() for t in TABLES}
    for table, name in cur.fetchall():
        # pg auto-generates `<col>_not_null` etc. for column-level NOT NULL
        # — those aren't named in the schema file and aren't real CHECKs in
        # pg_constraint anyway (NOT NULL lives on pg_attribute.attnotnull),
        # so this loop only sees user-defined CHECKs. Still, guard against
        # any future expansion.
        actual[table].add(name)

    errs: list[str] = []
    for table, expected in EXPECTED_CHECK_CONSTRAINTS.items():
        missing = sorted(expected - actual.get(table, set()))
        extra = sorted(actual.get(table, set()) - expected)
        if missing:
            errs.append(
                f"public.{table}: CHECK constraints declared in schema but "
                f"missing in DB: {missing}"
            )
        if extra:
            errs.append(
                f"public.{table}: CHECK constraints present in DB but not "
                f"declared in schema: {extra}"
            )
    return errs


def check_add_lead_column(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid "
        "WHERE n.nspname = 'public' AND p.proname = 'add_lead_column'"
    )
    rows = cur.fetchall()
    if not rows:
        return ["public.add_lead_column function is missing"]

    errs: list[str] = []
    secdef, owner, cfg = rows[0]
    if not secdef:
        errs.append("public.add_lead_column is not SECURITY DEFINER")
    if owner != "postgres":
        errs.append(
            f"public.add_lead_column owner is {owner!r}, expected 'postgres'"
        )
    if not any(s.lower().startswith("search_path=") for s in (cfg or [])):
        errs.append("public.add_lead_column has no search_path set")

    cur = conn.execute(
        "SELECT grantee, privilege_type "
        "FROM information_schema.role_routine_grants "
        "WHERE routine_schema = 'public' AND routine_name = 'add_lead_column' "
        "AND grantee IN ('anon', 'authenticated', 'PUBLIC')"
    )
    for grantee, priv in cur.fetchall():
        errs.append(
            f"public.add_lead_column has {priv} grant to {grantee} "
            f"(must be revoked)"
        )
    return errs


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2
    if not SCHEMA_FILE.is_file():
        print(f"ERROR: {SCHEMA_FILE} not found", file=sys.stderr)
        return 2

    expected = parse_expected_columns(SCHEMA_FILE)

    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    failures: list[str] = []
    try:
        actual = fetch_db_columns(conn)
        for t in TABLES:
            missing = sorted(expected[t] - actual[t])
            extra = sorted(actual[t] - expected[t])
            if missing:
                failures.append(
                    f"public.{t}: declared in schema, missing in DB: {missing}"
                )
            if extra:
                failures.append(
                    f"public.{t}: present in DB, not declared in schema: {extra}"
                )

        failures += check_rls(conn)
        failures += check_deny_policies(conn)
        failures += check_table_grants(conn)
        failures += check_check_constraints(conn)
        failures += check_add_lead_column(conn)
    finally:
        conn.close()

    if failures:
        print("Schema verification FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("Schema verification PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
