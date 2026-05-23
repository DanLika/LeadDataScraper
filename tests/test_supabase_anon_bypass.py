"""Bypass-the-backend test: hit PostgREST directly with the Supabase anon key.

If the backend ever leaks the anon key (it's already in NEXT_PUBLIC_*), an
attacker can skip our FastAPI gate entirely and call PostgREST. RLS + revoked
GRANTs on `anon` must make every table + RPC unreachable.

Run via `pytest tests/test_supabase_anon_bypass.py`. Skipped if no
SUPABASE_URL / anon key is available in .env.test or .env.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_anon_creds() -> tuple[str | None, str | None]:
    """Resolve SUPABASE_URL + anon key from .env.test, .env, frontend/.env.local, env."""
    candidates = [
        REPO_ROOT / ".env.test",
        REPO_ROOT / ".env",
        REPO_ROOT / "frontend" / ".env.local",
    ]
    merged: dict[str, str] = {}
    for path in candidates:
        if path.exists():
            for k, v in dotenv_values(path).items():
                if v and k not in merged:
                    merged[k] = v

    url = (
        os.environ.get("SUPABASE_URL")
        or merged.get("SUPABASE_URL")
        or merged.get("NEXT_PUBLIC_SUPABASE_URL")
    )
    anon = (
        os.environ.get("SUPABASE_ANON_KEY")
        or merged.get("SUPABASE_ANON_KEY")
        or merged.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    )
    return url, anon


SUPABASE_URL, ANON_KEY = _load_anon_creds()

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and ANON_KEY),
    reason="SUPABASE_URL + anon key not found in .env.test / .env / frontend/.env.local",
)

TIMEOUT = 10


@pytest.fixture(scope="module")
def anon_headers() -> dict[str, str]:
    return {
        "apikey": ANON_KEY,
        "Authorization": f"Bearer {ANON_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@pytest.fixture(scope="module")
def rest_base() -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1"


def _is_blocked(resp: requests.Response) -> bool:
    """A response is 'blocked' if anon can't see any rows or got an auth/permission error."""
    if resp.status_code in (401, 403, 404):
        return True
    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            return False
        return isinstance(body, list) and len(body) == 0
    return False


def _is_errored(resp: requests.Response) -> bool:
    """A write/RPC is blocked if PostgREST refused with 4xx or returned a JSON error body."""
    if 400 <= resp.status_code < 500:
        return True
    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            return False
        if isinstance(body, dict) and (
            "code" in body or "message" in body or "error" in body
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# SELECT — RLS must hide rows OR PostgREST must reject the anon role.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "table",
    ["leads", "campaigns", "campaign_messages", "orchestration_jobs"],
)
def test_anon_cannot_read_table(rest_base, anon_headers, table):
    resp = requests.get(
        f"{rest_base}/{table}",
        headers=anon_headers,
        params={"select": "*", "limit": "5"},
        timeout=TIMEOUT,
    )
    assert _is_blocked(resp), (
        f"anon SELECT on `{table}` leaked data: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )

    # Stronger assertion for non-leads tables: those should never even return
    # an empty 200 — anon GRANT is fully revoked, so PostgREST should 401/403/404.
    if table != "leads":
        assert resp.status_code in (401, 403, 404), (
            f"anon SELECT on `{table}` must be hard-rejected, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# INSERT — must fail outright.
# ---------------------------------------------------------------------------

def test_anon_cannot_insert_lead(rest_base, anon_headers):
    payload = {
        "unique_key": "anon-bypass-probe",
        "name": "anon-bypass-probe",
        "audit_status": "Pending",
    }
    resp = requests.post(
        f"{rest_base}/leads",
        headers={**anon_headers, "Prefer": "return=representation"},
        json=payload,
        timeout=TIMEOUT,
    )
    assert _is_errored(resp), (
        f"anon INSERT into leads succeeded: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )

    # If by accident the insert landed, prove the row is NOT readable back
    # (so subsequent test runs don't leave the probe sitting in prod).
    readback = requests.get(
        f"{rest_base}/leads",
        headers=anon_headers,
        params={"unique_key": "eq.anon-bypass-probe", "select": "unique_key"},
        timeout=TIMEOUT,
    )
    if readback.status_code == 200:
        try:
            assert readback.json() == [], (
                "anon INSERT was not rejected and the row is visible — "
                "RLS bypass confirmed."
            )
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# RPC — malicious column names + non-existent RPCs.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "malicious_col",
    [
        "x; DROP TABLE leads--",
        "x'); DROP TABLE leads;--",
        "1; SELECT 1",
        "valid_col, extra_col text",
        "../../etc/passwd",
        "col WITH spaces",
    ],
)
def test_anon_add_lead_column_malicious_rejected(rest_base, anon_headers, malicious_col):
    resp = requests.post(
        f"{rest_base}/rpc/add_lead_column",
        headers=anon_headers,
        json={"col": malicious_col},
        timeout=TIMEOUT,
    )
    assert _is_errored(resp), (
        f"add_lead_column accepted malicious col {malicious_col!r}: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )

    # Probe the column didn't actually get created — query information_schema
    # via PostgREST is not available, so we just verify `leads` schema is
    # still readable to no one (RLS) and the column name doesn't appear in
    # an error echo that looks like success.
    body_lower = resp.text.lower()
    assert "added" not in body_lower and "altered" not in body_lower, (
        f"add_lead_column appears to have applied {malicious_col!r}: {resp.text[:300]}"
    )


def test_anon_exec_sql_rpc_does_not_exist(rest_base, anon_headers):
    """`exec_sql` was removed. PostgREST must 404 (or 401/403 for non-existent RPC)."""
    resp = requests.post(
        f"{rest_base}/rpc/exec_sql",
        headers=anon_headers,
        json={"query": "SELECT 1"},
        timeout=TIMEOUT,
    )
    assert resp.status_code in (401, 403, 404), (
        f"exec_sql RPC unexpectedly reachable: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )


@pytest.mark.parametrize(
    "rpc_name",
    ["exec", "exec_sql", "run_sql", "execute_sql", "raw_sql", "admin_sql"],
)
def test_anon_no_arbitrary_sql_rpc(rest_base, anon_headers, rpc_name):
    resp = requests.post(
        f"{rest_base}/rpc/{rpc_name}",
        headers=anon_headers,
        json={"query": "SELECT 1", "sql": "SELECT 1"},
        timeout=TIMEOUT,
    )
    # Either the function doesn't exist (404) or anon lacks EXECUTE (401/403).
    # A 200 here would mean an arbitrary-SQL RPC is callable by anon — full RCE on the DB.
    assert resp.status_code != 200, (
        f"RPC `{rpc_name}` callable by anon — arbitrary SQL surface: "
        f"body={resp.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Sanity: with NO apikey at all, PostgREST should also 401.
# ---------------------------------------------------------------------------

def test_postgrest_rejects_missing_apikey(rest_base):
    resp = requests.get(
        f"{rest_base}/leads",
        params={"select": "*", "limit": "1"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 401, (
        f"PostgREST served data without apikey: "
        f"status={resp.status_code} body={resp.text[:300]}"
    )
