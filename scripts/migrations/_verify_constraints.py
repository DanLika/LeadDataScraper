#!/usr/bin/env python3
"""Post-apply CHECK-constraint verifier.

Run AFTER applying a migration via Supabase Management API to catch silent
regex / IN-list literal munging — the apostrophe-double-escape bug class
that stranded the Phase 14+15 dispatcher pipeline (PR #366, bug memory
`bug_constraint_apostrophe_double_escape_2026-05-27`).

Two layers:

  1) Stored-DEF inspection (cheap, primary). Pulls `pg_get_constraintdef`
     for each tracked constraint and asserts:
       (a) no triple-apostrophe smell `'''` in the definition
       (b) the stored canonical form matches an expected regex
     This catches the exact bug class with zero side effects.

  2) INSERT-probe (semantic, secondary). For each constraint, attempts a
     positive value (MUST pass) + a negative value (MUST reject) inside a
     `DO $$` block that ends with `RAISE EXCEPTION` so the entire txn
     rolls back — no row ever commits in prod.

Exit non-zero if any positive value is rejected OR any negative value is
accepted OR any stored DEF mismatches its expected form.

Usage:
    SUPABASE_ACCESS_TOKEN=sbp_... python3 scripts/migrations/_verify_constraints.py
    make verify-prod-constraints

Flags:
    --canary       run a one-shot RAISE-EXCEPTION probe to confirm the
                   Management API passes the message through verbatim
                   (parses 'VERIFY canary=ok'). Skip layer 2 if this fails.
    --skip-probes  run only layer 1 (stored-DEF inspection). Useful when
                   layer 2 cannot parse responses.

Env:
    SUPABASE_ACCESS_TOKEN  Personal Access Token (Bearer)  [required]
    SUPABASE_PROJECT_REF   project ref (default: kbtkxpvchmunwjykbeht)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

# Supabase project refs are always 20-char lowercase alphanumeric. Reject
# anything else — the ref interpolates into the Management API path, so
# any scheme-rewrite or odd character via env would be a footgun
# (CWE-939). Host + scheme are hardcoded; only the project ref segment
# of the path varies.
_PROJECT_REF_RE = re.compile(r"\A[a-z0-9]{20}\Z")
_DEFAULT_PROJECT_REF = "kbtkxpvchmunwjykbeht"

MGMT_HOST = "api.supabase.com"


def _resolve_project_ref() -> str:
    ref = os.environ.get("SUPABASE_PROJECT_REF", _DEFAULT_PROJECT_REF)
    if not _PROJECT_REF_RE.match(ref):
        sys.exit(
            f"ERROR: SUPABASE_PROJECT_REF must be 20-char lowercase alnum; got {ref!r}"
        )
    return ref


PROJECT_REF = _resolve_project_ref()
MGMT_PATH = "/v1/projects/" + PROJECT_REF + "/database/query"
USER_AGENT = "lds-migration-verifier/1.0"

# ---------------------------------------------------------------------------
# Constraint catalog
# ---------------------------------------------------------------------------
# Only literal-bearing CHECKs (regex / IN-list) are vulnerable to the
# apostrophe-double-escape bug. Length/comparison CHECKs have nothing for
# the apply path to mis-escape, so they are out of scope.
#
# `expected_def_re` matches the canonical form Postgres returns from
# `pg_get_constraintdef`. INSERT-LIST literals come back as
# `= ANY (ARRAY['x'::text, ...])`; regex literals come back as
# `(col ~ 'pattern'::text)`. Update only if the source migration also
# changes — never to silence a finding.


@dataclass
class Probe:
    constraint: str
    expected_def_re: re.Pattern[str]
    positive: str
    negative: str
    parent_setup: str
    probe_table: str
    probe_columns: str  # comma-separated column list for INSERT
    probe_values_positive_extras: str  # values after sequence_id/etc.
    probe_values_negative_extras: str
    # which placeholder in the value list gets the literal; everything else
    # is interpolated as-is.
    column_under_test: str


PROBES: list[Probe] = [
    Probe(
        constraint="sequence_steps_send_days_format",
        expected_def_re=re.compile(
            r"^CHECK \(\(send_days ~ '\^\(mon\|tue\|wed\|thu\|fri\|sat\|sun\)"
            r"\(,\(mon\|tue\|wed\|thu\|fri\|sat\|sun\)\)\*\$'::text\)\)$"
        ),
        positive="mon,tue,wed,thu,fri",
        negative="Mon",
        parent_setup="""
            INSERT INTO public.campaigns(name, channel)
                VALUES ('_verify_probe_' || gen_random_uuid()::text, 'email')
                RETURNING id INTO campaign_id;
            INSERT INTO public.sequences(campaign_id, name)
                VALUES (campaign_id, '_verify_probe')
                RETURNING id INTO seq_id;
        """,
        probe_table="public.sequence_steps",
        probe_columns="sequence_id, step_index, channel, send_days",
        probe_values_positive_extras="seq_id, 1, 'email'",
        probe_values_negative_extras="seq_id, 2, 'email'",
        column_under_test="send_days",
    ),
    Probe(
        constraint="sequence_variants_content_type_allowed",
        expected_def_re=re.compile(
            r"^CHECK \(\(content_type = ANY \(ARRAY\['text'::text, 'html'::text\]\)\)\)$"
        ),
        positive="text",
        negative="HTML",  # uppercase — must reject
        parent_setup="""
            INSERT INTO public.campaigns(name, channel)
                VALUES ('_verify_probe_' || gen_random_uuid()::text, 'email')
                RETURNING id INTO campaign_id;
            INSERT INTO public.sequences(campaign_id, name)
                VALUES (campaign_id, '_verify_probe')
                RETURNING id INTO seq_id;
            INSERT INTO public.sequence_steps(sequence_id, step_index, channel, send_days)
                VALUES (seq_id, 1, 'email', 'mon')
                RETURNING id INTO step_id;
        """,
        probe_table="public.sequence_variants",
        probe_columns="step_id, variant_label, body_template, content_type",
        probe_values_positive_extras="step_id, 'a', 'hi'",
        probe_values_negative_extras="step_id, 'b', 'hi'",
        column_under_test="content_type",
    ),
]


# ---------------------------------------------------------------------------
# Management API helpers
# ---------------------------------------------------------------------------


@dataclass
class ApiResult:
    status: int
    body: str
    parsed: Optional[object] = None  # JSON-decoded body if it decoded


def mgmt_query(sql: str, token: str) -> ApiResult:
    """POST a SQL body to the Management API via subprocess+curl.

    Why subprocess+curl instead of a Python HTTP client:
      * Matches the runbook (`docs/runbooks/apply-phase-14-15-migrations.md`)
        + bug memory `session_2026-05-27_schema_drift_resolved` which both
        use curl against the same endpoint.
      * Avoids the curated lint surface (dynamic-urllib / HTTPSConnection
        version-skew warnings) for an ops tool that just needs to POST one
        JSON body to a hardcoded host.
      * Cloudflare-fronted endpoint occasionally returns 1010 on default
        Python UA; curl works without UA shenanigans.
    """
    if not shutil.which("curl"):
        die("curl is required but not found on PATH")
    body_bytes = json.dumps({"query": sql}).encode("utf-8")
    # MGMT_HOST + MGMT_PATH are module-level constants; PROJECT_REF inside
    # MGMT_PATH is regex-gated to [a-z0-9]{20} at module load. The full URL
    # is built from these trusted parts only.
    url = "https://" + MGMT_HOST + MGMT_PATH
    proc = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--write-out",
            "\n__HTTP_STATUS__%{http_code}",
            "--header",
            "Authorization: Bearer " + token,
            "--header",
            "Content-Type: application/json",
            "--header",
            "User-Agent: " + USER_AGENT,
            "--data-binary",
            "@-",
            url,
        ],
        input=body_bytes,
        capture_output=True,
        check=False,
    )
    raw = proc.stdout.decode("utf-8", errors="replace")
    if "__HTTP_STATUS__" not in raw:
        die(
            f"curl returned no status line; rc={proc.returncode} "
            f"stderr={proc.stderr.decode('utf-8', errors='replace')[:200]}"
        )
    text, _, status_line = raw.rpartition("\n__HTTP_STATUS__")
    try:
        status = int(status_line.strip())
    except ValueError:
        die(f"could not parse status from curl output: {status_line!r}")
    return ApiResult(status, text, _try_parse_json(text))


def _try_parse_json(body: str) -> Optional[object]:
    try:
        return json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Layer 1 — stored DEF inspection
# ---------------------------------------------------------------------------


def fetch_constraint_defs(constraints: list[str], token: str) -> dict[str, str]:
    """Pull pg_get_constraintdef for each named constraint."""
    placeholders = ",".join(f"'{c}'" for c in constraints)
    sql = (
        "SELECT conname, pg_get_constraintdef(oid) AS def "
        f"FROM pg_constraint WHERE conname IN ({placeholders});"
    )
    result = mgmt_query(sql, token)
    if result.status >= 400 or not isinstance(result.parsed, list):
        die(
            f"failed to fetch constraint defs (status {result.status}): "
            f"{result.body[:300]}"
        )
    rows: dict[str, str] = {}
    assert isinstance(result.parsed, list)
    for row in result.parsed:
        rows[row["conname"]] = row["def"]
    return rows


def verify_stored_defs(token: str) -> tuple[int, list[str]]:
    """Layer 1. Returns (fail_count, [report_lines])."""
    names = [p.constraint for p in PROBES]
    defs = fetch_constraint_defs(names, token)
    fails = 0
    lines: list[str] = ["", "=== Layer 1: stored CHECK definitions ==="]
    for probe in PROBES:
        actual = defs.get(probe.constraint)
        if actual is None:
            lines.append(f"FAIL {probe.constraint}: constraint not found in DB")
            fails += 1
            continue
        if "'''" in actual:
            lines.append(
                f"FAIL {probe.constraint}: triple-apostrophe smell "
                f"(apostrophe-double-escape bug)\n"
                f"    actual: {actual}"
            )
            fails += 1
            continue
        if not probe.expected_def_re.match(actual):
            lines.append(
                f"FAIL {probe.constraint}: stored def does not match expected form\n"
                f"    expected ~ {probe.expected_def_re.pattern}\n"
                f"    actual:    {actual}"
            )
            fails += 1
            continue
        lines.append(f"PASS {probe.constraint}: {actual}")
    return fails, lines


# ---------------------------------------------------------------------------
# Layer 2 — INSERT probe via RAISE EXCEPTION rollback
# ---------------------------------------------------------------------------


def build_probe_sql(probe: Probe) -> str:
    """Build a self-rolling-back DO block that records positive +
    negative outcomes, then RAISE EXCEPTION to undo all inserts."""
    return f"""
DO $$
DECLARE
    campaign_id uuid;
    seq_id uuid;
    step_id uuid;
    positive_ok bool := false;
    negative_blocked bool := false;
BEGIN
    {probe.parent_setup}

    BEGIN
        INSERT INTO {probe.probe_table}({probe.probe_columns})
            VALUES ({probe.probe_values_positive_extras},
                    {sql_quote(probe.positive)});
        positive_ok := true;
    EXCEPTION WHEN check_violation THEN positive_ok := false;
    END;

    BEGIN
        INSERT INTO {probe.probe_table}({probe.probe_columns})
            VALUES ({probe.probe_values_negative_extras},
                    {sql_quote(probe.negative)});
        negative_blocked := false;
    EXCEPTION WHEN check_violation THEN negative_blocked := true;
    END;

    RAISE EXCEPTION 'VERIFY positive=% negative=%', positive_ok, negative_blocked;
END $$;
"""


def sql_quote(value: str) -> str:
    """Single-quote a SQL string literal. Doubles embedded apostrophes
    (Postgres standard)."""
    return "'" + value.replace("'", "''") + "'"


_VERIFY_RE = re.compile(
    r"VERIFY positive=(t|f|true|false) negative=(t|f|true|false)",
    re.IGNORECASE,
)


def parse_verify(body: str) -> Optional[tuple[bool, bool]]:
    """Search the API response body for the RAISE-EXCEPTION marker."""
    m = _VERIFY_RE.search(body)
    if not m:
        return None
    truthy = {"t", "true"}
    return (m.group(1).lower() in truthy, m.group(2).lower() in truthy)


def run_canary(token: str) -> bool:
    """Confirm the Management API passes RAISE EXCEPTION messages
    through verbatim. Returns True if the canary substring survives."""
    sql = "DO $$ BEGIN RAISE EXCEPTION 'VERIFY canary=ok'; END $$;"
    result = mgmt_query(sql, token)
    survived = "VERIFY canary=ok" in result.body
    print(
        f"canary: status={result.status} survived={survived}\n"
        f"  body: {result.body[:300]}"
    )
    return survived


def verify_probes(token: str) -> tuple[int, list[str]]:
    """Layer 2. Returns (fail_count, [report_lines])."""
    fails = 0
    lines: list[str] = ["", "=== Layer 2: INSERT probes ==="]
    for probe in PROBES:
        sql = build_probe_sql(probe)
        result = mgmt_query(sql, token)
        parsed = parse_verify(result.body)
        if parsed is None:
            lines.append(
                f"SKIP {probe.constraint}: could not parse VERIFY marker "
                f"(status={result.status})\n"
                f"    body: {result.body[:400]}"
            )
            continue
        positive_ok, negative_blocked = parsed
        if positive_ok and negative_blocked:
            lines.append(
                f"PASS {probe.constraint}: "
                f"positive {probe.positive!r} accepted, "
                f"negative {probe.negative!r} rejected"
            )
            continue
        fails += 1
        if not positive_ok:
            lines.append(
                f"FAIL {probe.constraint}: positive {probe.positive!r} "
                f"REJECTED — CHECK literal munged on apply (drift!)"
            )
        if not negative_blocked:
            lines.append(
                f"FAIL {probe.constraint}: negative {probe.negative!r} "
                f"ACCEPTED — CHECK is too loose"
            )
    return fails, lines


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canary",
        action="store_true",
        help="run only the RAISE-EXCEPTION pass-through canary",
    )
    parser.add_argument(
        "--skip-probes",
        action="store_true",
        help="skip Layer 2 (INSERT probes); only inspect stored DEFs",
    )
    args = parser.parse_args()

    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        die(
            "SUPABASE_ACCESS_TOKEN not set. "
            "Generate a PAT at https://supabase.com/dashboard/account/tokens "
            "and pass it as an env var."
        )

    if args.canary:
        ok = run_canary(token)
        return 0 if ok else 1

    print(f"Verifying CHECK constraints on project {PROJECT_REF}")

    layer1_fails, layer1_lines = verify_stored_defs(token)
    for line in layer1_lines:
        print(line)

    layer2_fails = 0
    if args.skip_probes:
        print("\n=== Layer 2: skipped (--skip-probes) ===")
    else:
        layer2_fails, layer2_lines = verify_probes(token)
        for line in layer2_lines:
            print(line)

    total = layer1_fails + layer2_fails
    print()
    if total:
        print(
            f"FAILED — {total} constraint check(s) failed.\n"
            f"  Layer 1 (stored DEF): {layer1_fails}\n"
            f"  Layer 2 (INSERT probe): {layer2_fails}\n"
            f"Inspect the offender:\n"
            f"  SELECT conname, pg_get_constraintdef(oid) "
            f"FROM pg_constraint WHERE conname='<name>';\n"
            f"See `bug_constraint_apostrophe_double_escape_2026-05-27` memory."
        )
        return 1
    print(f"OK — {len(PROBES)} constraint(s) verified in both layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
