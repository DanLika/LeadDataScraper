"""IDOR + path-traversal + enumeration sweep against `/campaigns/{id}` and
sibling routes.

Threat model: this is a single-tenant deployment (CLAUDE.md
`_assert_single_tenant_if_enforced`), so there's no "operator B" in the
product. The IDOR concern reduces to: an attacker who *leaks* the API key
or guesses a campaign UUID must not bypass auth, enumerate resource state,
or trip the route into a 500 that leaks structural information.

Assertions:
1. **Wrong API key → 401/403.** The `verify_api_key` dependency uses
   `secrets.compare_digest` (CLAUDE.md), so any prefix-mutated key fails
   constant-time-compare and the route never reaches the DB.
2. **Sequential UUID enumeration → no timing leak.** Existing and
   non-existing IDs respond within a bounded delta (no 200-branch
   amplifier like a 10-row join leaks "this row exists").
3. **Path traversal in `{id}` → 404 or 422, never 500.** A 500 means an
   unsanitised string reached PostgREST and threw — that signals
   missing input validation on the route, even if the global handler
   masks the body. Filing this as a test enforces the invariant.
4. **Extra params ignored.** `?owner=*`, `?id[]=evil`, duplicate `id=`
   must not change the response shape.

Opt-in (creates + deletes a throw-away campaign):
  RUN_IDOR_SWEEP=1
  BACKEND_URL=http://localhost:8000
  API_SECRET_KEY=<must match backend>
  SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY  (for direct campaign seed/cleanup)
"""

from __future__ import annotations

import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import pytest
import requests
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 10


def _load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in [REPO_ROOT / ".env.test", REPO_ROOT / ".env"]:
        if path.exists():
            for k, v in dotenv_values(path).items():
                if v and k not in merged:
                    merged[k] = v
    for k in (
        "RUN_IDOR_SWEEP",
        "BACKEND_URL",
        "API_SECRET_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
    ):
        v = os.environ.get(k)
        if v:
            merged[k] = v
    return merged


ENV = _load_env()
OPT_IN = ENV.get("RUN_IDOR_SWEEP", "").strip() in ("1", "true", "yes")
BACKEND_URL = (ENV.get("BACKEND_URL") or "").rstrip("/")
API_KEY = ENV.get("API_SECRET_KEY", "")

pytestmark = pytest.mark.skipif(
    not (OPT_IN and BACKEND_URL and API_KEY),
    reason=(
        "Set RUN_IDOR_SWEEP=1 + BACKEND_URL + API_SECRET_KEY to run the "
        "IDOR sweep. Skipping."
    ),
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _service_client():
    url = ENV.get("SUPABASE_URL") or ENV.get("NEXT_PUBLIC_SUPABASE_URL")
    key = ENV.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return None
    try:
        from supabase import create_client

        return create_client(url, key)
    except Exception:
        return None


def _h(*, key: Optional[str] = None) -> dict[str, str]:
    return {
        "X-API-Key": key if key is not None else API_KEY,
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="module")
def seeded_campaign_id():
    """Create a throw-away campaign via service-role. Tears down after the
    module runs. Yields the UUID."""
    svc = _service_client()
    if svc is None:
        pytest.skip("SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY required to seed/cleanup")
    cid = str(uuid.uuid4())
    svc.table("campaigns").insert(
        {
            "id": cid,
            "name": f"idor-sweep-{cid[:8]}",
            "segment": "all",
            "status": "draft",
        }
    ).execute()
    try:
        yield cid
    finally:
        svc.table("campaigns").delete().eq("id", cid).execute()


# ---------------------------------------------------------------------------
# 1) Wrong / mutated API key → 401/403 on read AND on state-change.
# ---------------------------------------------------------------------------


# Parametrize ids are deliberately opaque labels — pytest collection prints
# IDs to stdout/CI logs, so derived values (real key with one char swapped,
# real key + suffix) MUST NOT appear in the id. The bad_key value itself
# stays inside the test function and is never logged.
@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        ("first-char-mutated", lambda: "x" + API_KEY[1:] if API_KEY else "x"),
        ("last-char-mutated", lambda: API_KEY[:-1] + "Z" if API_KEY else "Z"),
        ("with-suffix", lambda: API_KEY + "extra" if API_KEY else "extra"),
        "0" * 64,
        ("bearer-prefix", lambda: "Bearer " + API_KEY if API_KEY else "Bearer x"),
    ],
    ids=[
        "empty",
        "first-char-mutated",
        "last-char-mutated",
        "with-suffix",
        "all-zeros",
        "bearer-prefix",
    ],
)
def test_wrong_api_key_blocks_read(seeded_campaign_id, bad_key):
    if isinstance(bad_key, tuple):
        bad_key = bad_key[1]()
    if bad_key == API_KEY:  # parametrize edge — never use the real key here
        pytest.skip("bad_key generator collapsed to the real key")
    r = requests.get(
        f"{BACKEND_URL}/campaigns/{seeded_campaign_id}",
        headers=_h(key=bad_key),
        timeout=TIMEOUT,
    )
    assert r.status_code in (401, 403), (
        f"Wrong API key reached the campaign read: status={r.status_code} "
        f"body={r.text[:200]}"
    )


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        ("first-char-mutated", lambda: "x" + API_KEY[1:] if API_KEY else "x"),
        "0" * 64,
    ],
    ids=["empty", "first-char-mutated", "all-zeros"],
)
def test_wrong_api_key_blocks_state_change(seeded_campaign_id, bad_key):
    if isinstance(bad_key, tuple):
        bad_key = bad_key[1]()
    """POST /campaigns/{id}/start with a bad key must 401/403 BEFORE any
    DB mutation. We verify the row's `status` is still `draft` after."""
    svc = _service_client()
    r = requests.post(
        f"{BACKEND_URL}/campaigns/{seeded_campaign_id}/start",
        headers=_h(key=bad_key),
        timeout=TIMEOUT,
    )
    assert r.status_code in (401, 403), (
        f"Wrong API key reached state change: {r.status_code}"
    )
    if svc is not None:
        row = (
            svc.table("campaigns")
            .select("status")
            .eq("id", seeded_campaign_id)
            .maybe_single()
            .execute()
            .data
        )
        assert row is not None
        assert row["status"] == "draft", (
            f"State changed despite auth rejection: {row['status']!r}"
        )


# ---------------------------------------------------------------------------
# 2) UUID enumeration timing — no info leak beyond the 200/404 status code.
# ---------------------------------------------------------------------------


def test_uuid_enumeration_timing_uniform(seeded_campaign_id):
    """For 20 random non-existent UUIDs + 5 hits on the existing one,
    response-time distributions must overlap. A 200-branch that fans out
    into N extra queries would let an attacker tell existence by timing
    even when status codes are normalised.

    Loose bound: max(any sample) - min(any sample) ≤ 1.5s. Tighter would
    flake on network jitter; this still catches a 5×-amplification
    regression."""
    samples_existing: list[float] = []
    samples_missing: list[float] = []

    for _ in range(5):
        t0 = time.perf_counter()
        r = requests.get(
            f"{BACKEND_URL}/campaigns/{seeded_campaign_id}",
            headers=_h(),
            timeout=TIMEOUT,
        )
        samples_existing.append(time.perf_counter() - t0)
        assert r.status_code == 200, f"sanity probe: {r.status_code}"

    for _ in range(20):
        ghost = str(uuid.uuid4())
        t0 = time.perf_counter()
        r = requests.get(
            f"{BACKEND_URL}/campaigns/{ghost}",
            headers=_h(),
            timeout=TIMEOUT,
        )
        samples_missing.append(time.perf_counter() - t0)
        assert r.status_code == 404, (
            f"Ghost UUID {ghost} returned {r.status_code} body={r.text[:120]}"
        )

    all_samples = samples_existing + samples_missing
    spread = max(all_samples) - min(all_samples)
    assert spread < 1.5, (
        f"Existence-probe timing spread {spread:.3f}s exceeds 1.5s — "
        f"existing={[f'{t:.3f}' for t in samples_existing]} "
        f"missing-mean={statistics.mean(samples_missing):.3f}s"
    )


# ---------------------------------------------------------------------------
# 3) Path traversal in {id} — 404 or 422, never 500.
# ---------------------------------------------------------------------------

# Each item is what the client sends in the URL path. requests will %-encode
# unreserved chars, but most of these are already encoded or are pure ASCII
# that maps unchanged. The point: every malformed/hostile value must be
# rejected by the route, not crashed-then-masked by the global 500 handler.
PATH_TRAVERSAL_IDS = [
    "../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..\\..\\windows\\system32",
    "not-a-uuid",
    "0",
    "null",
    "undefined",
    "00000000-0000-0000-0000-00000000000g",  # invalid hex char
    "00000000-0000-0000-0000-0000000000000",  # too long
    "{id}",
    "%00",
    "abc%00.json",
    "/etc/passwd",
    "x" * 4096,  # oversized
]


@pytest.mark.parametrize("bad_id", PATH_TRAVERSAL_IDS)
def test_path_traversal_in_id_does_not_500(bad_id):
    # Use the raw value as-is in the URL path; requests percent-encodes
    # safe chars and leaves already-encoded sequences alone.
    safe = quote(bad_id, safe="%")
    r = requests.get(
        f"{BACKEND_URL}/campaigns/{safe}",
        headers=_h(),
        timeout=TIMEOUT,
    )
    assert r.status_code != 500, (
        f"Malformed id {bad_id!r} crashed the route → 500. Body: {r.text[:200]}"
    )
    # 404 (row not found) or 422 (FastAPI route param rejected) are the
    # two safe outcomes. 400 acceptable if a future hardening adds explicit
    # validation. Anything else (200, 500, 502) is a finding.
    assert r.status_code in (400, 404, 422), (
        f"Unexpected status for malformed id {bad_id!r}: {r.status_code}"
    )


# ---------------------------------------------------------------------------
# 4) Extra params ignored.
# ---------------------------------------------------------------------------

EXTRA_PARAM_VARIANTS = [
    "?owner=*",
    "?owner_user_id=00000000-0000-0000-0000-000000000000",
    "?id=other-campaign-id",
    "?id[]=evil",
    "?role=service_role",
    "?filter=*",
    "?bypass_auth=1",
]


@pytest.mark.parametrize("query", EXTRA_PARAM_VARIANTS)
def test_extra_query_params_do_not_change_response(seeded_campaign_id, query):
    """The route signature is `(campaign_id: str)` — no other query params
    are declared. Extra params must be silently ignored. We verify the
    response is byte-identical (same status, same JSON body) to the
    no-extra-params baseline."""
    baseline = requests.get(
        f"{BACKEND_URL}/campaigns/{seeded_campaign_id}",
        headers=_h(),
        timeout=TIMEOUT,
    )
    variant = requests.get(
        f"{BACKEND_URL}/campaigns/{seeded_campaign_id}{query}",
        headers=_h(),
        timeout=TIMEOUT,
    )
    assert variant.status_code == baseline.status_code, (
        f"Extra-param variant {query!r} flipped status: "
        f"baseline={baseline.status_code} variant={variant.status_code}"
    )
    # JSON-level equality — handles dict-key ordering differences. If the
    # extra param changed the row returned, this catches it.
    try:
        base_j = baseline.json()
        var_j = variant.json()
    except ValueError:
        pytest.fail(f"Non-JSON response for variant {query!r}: {variant.text[:200]}")
    assert base_j == var_j, f"Extra-param variant {query!r} changed response payload."


# ---------------------------------------------------------------------------
# 5) Coupling test — the real key + real id WORKS. Without this, every
# auth test could be passing trivially because the backend is down.
# ---------------------------------------------------------------------------


def test_legit_key_reads_own_campaign(seeded_campaign_id):
    r = requests.get(
        f"{BACKEND_URL}/campaigns/{seeded_campaign_id}",
        headers=_h(),
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, (
        f"Legit key + real id failed: {r.status_code} {r.text[:200]}"
    )
    body = r.json()
    assert body["campaign"]["id"] == seeded_campaign_id
