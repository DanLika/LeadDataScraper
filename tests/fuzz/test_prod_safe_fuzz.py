"""Tier 2 prod fuzz — side-effect-free surfaces only.

Default-skipped. Opt in with `RUN_PROD_FUZZ=1` and pass the
operator's API key via `RUN_PROD_FUZZ_API_KEY=<64-char>`. CI never
sets either — this file is documentation + a reusable harness for
the operator to re-run after envelope-shape changes ship.

Targets (all read-only or no-side-effect):

  - `POST /metrics`  — `WebVitalsMetric` beacon. Logs only, no DB.
  - `GET  /leads`     — cursor + limit fuzz. Cursor escape vectors
                        already pinned by `tests/security/test_cursor_escape.py`;
                        this is the envelope-shape complement.
  - `GET  /orchestrator/status/{job_id}` — UUID-fuzzed; expects 404/422.
  - `GET  /unsubscribe/{token}` — public HTML, fuzz with random tokens.

Banned: anything that writes (POSTs to `/upload`, `/webhooks/instantly`,
`/discovery/start`, `/process-*`, `/draft-*`, `/hunt-*`, `/execute`,
`/ask`, `/campaigns`, `/operator/*`, `/leads/clear`, `/leads/demo`,
`/orchestrator/start`, `/enrich/start`). See the data-loss-audit
memory entry for the rationale.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

# `pytestmark` only skips test execution, not collection — pytest still
# imports the module. Use `importorskip` to skip collection entirely if
# hypothesis isn't installed (matches Tier 1).
pytest.importorskip("hypothesis_jsonschema")

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_PROD_FUZZ"),
    reason="prod fuzz disabled; set RUN_PROD_FUZZ=1 + RUN_PROD_FUZZ_API_KEY=...",
)

import httpx  # noqa: E402
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402
from hypothesis_jsonschema import from_schema  # noqa: E402

# Set sentinel env so we can import the model definitions for from_schema
# without `backend.main` blowing up on missing prod secrets.
os.environ.setdefault("API_SECRET_KEY", "x" * 64)
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x" * 40)
os.environ.setdefault("GEMINI_API_KEY", "x" * 39)
os.environ.setdefault("ADMIN_TOKEN", "x" * 32)
os.environ.setdefault("INSTANTLY_API_KEY", "x" * 32)
os.environ.setdefault("INSTANTLY_WEBHOOK_SECRET", "x" * 32)

from backend.main import WebVitalsMetric  # noqa: E402

_PROD_URL = os.getenv(
    "RUN_PROD_FUZZ_URL", "https://lead-scraper-backend-x51l.onrender.com"
)
_API_KEY = os.getenv("RUN_PROD_FUZZ_API_KEY") or ""

# Slow-pace the fuzz so we don't trip `/metrics` 60/min or `/leads` (10/min).
# 50 cases at 1.2s spacing = 60s wall clock; under all current rate limits.
_BASE_DELAY_S = float(os.getenv("RUN_PROD_FUZZ_DELAY", "1.2"))

_FUZZ_SETTINGS = settings(
    max_examples=int(os.getenv("RUN_PROD_FUZZ_EXAMPLES", "50")),
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
)


# Documented envelope shapes from `backend/main.py:583-654`.
def _assert_envelope(resp: httpx.Response, *, path: str) -> None:
    """Stop-condition matrix:

    - 5xx + status==500 + body=={"error":"Internal server error"} →
      bug. Any 5xx is suspicious; the test surfaces it.
    - 2xx: body must be JSON-decodable for backend routes; HTML for
      `/unsubscribe/{token}` (handled at the caller).
    - 4xx: body must match one of the documented envelopes
      (`{detail: ...}` for 422/403, `{error: ...}` for 400/413/429/503).
    """
    if resp.status_code == 500:
        # The hypothesis stop-condition: don't try to keep going.
        raise AssertionError(
            f"500 from {path!r}: body={resp.text[:400]!r} headers={dict(resp.headers)}"
        )
    if resp.status_code >= 500:
        # 502/503/504 are envelope-shape valid but operationally relevant.
        # Don't fail the run — record + continue. (`error_response` shape.)
        return
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"non-JSON body from {path!r} status={resp.status_code}: "
            f"{type(exc).__name__}: {exc}; raw={resp.text[:200]!r}"
        ) from exc
    if resp.status_code in (200, 201, 202, 204):
        return
    # 4xx — must be `{detail: ...}` (Pydantic / authz) or `{error: ...}`.
    if not isinstance(body, dict):
        raise AssertionError(
            f"non-object body from {path!r} status={resp.status_code}: {body!r}"
        )
    if "detail" not in body and "error" not in body:
        raise AssertionError(
            f"envelope drift on {path!r} status={resp.status_code}: keys={list(body)!r}"
        )


@pytest.fixture(scope="module")
def client() -> Any:
    if not _API_KEY:
        pytest.skip("RUN_PROD_FUZZ_API_KEY not set")
    with httpx.Client(
        base_url=_PROD_URL,
        headers={"X-API-Key": _API_KEY, "User-Agent": "lds-fuzz/1"},
        timeout=20.0,
    ) as c:
        yield c


# --- POST /metrics ---------------------------------------------------------


def test_prod_metrics_envelope(client: httpx.Client) -> None:
    strategy = from_schema(WebVitalsMetric.model_json_schema())
    state: dict[str, int] = {"sent": 0, "2xx": 0, "4xx": 0, "5xx": 0, "429": 0}

    @_FUZZ_SETTINGS
    @given(strategy)
    def _run(payload: dict) -> None:
        time.sleep(_BASE_DELAY_S)
        resp = client.post("/metrics", json=payload)
        state["sent"] += 1
        if resp.status_code == 429:
            state["429"] += 1
            return
        bucket = (
            "2xx" if 200 <= resp.status_code < 300
            else "4xx" if 400 <= resp.status_code < 500
            else "5xx"
        )
        state[bucket] += 1
        _assert_envelope(resp, path="/metrics")

    _run()
    print(f"\n[fuzz] /metrics: {state}")
    assert state["5xx"] == 0, state


# --- GET /leads cursor+limit ----------------------------------------------


def test_prod_leads_cursor_limit_envelope(client: httpx.Client) -> None:
    # Fuzz cursor against the known reject corpus + random base64-ish.
    cursor_strategy = st.one_of(
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",), min_codepoint=0, max_codepoint=0xFFFF
            ),
            max_size=600,
        ),
        st.binary(max_size=200).map(lambda b: b.hex()),
    )
    limit_strategy = st.one_of(
        st.integers(min_value=-(2**31), max_value=2**31),
        st.text(max_size=32),
        st.sampled_from(["", "abc", "1.5", "1e9", "NaN", "-1", "0"]),
    )

    state: dict[str, int] = {"sent": 0, "2xx": 0, "4xx": 0, "5xx": 0}

    @_FUZZ_SETTINGS
    @given(cursor_strategy, limit_strategy)
    def _run(cursor: str, limit: object) -> None:
        time.sleep(_BASE_DELAY_S)
        resp = client.get(
            "/leads", params={"cursor": cursor, "limit": str(limit)[:64]}
        )
        state["sent"] += 1
        bucket = (
            "2xx" if 200 <= resp.status_code < 300
            else "4xx" if 400 <= resp.status_code < 500
            else "5xx"
        )
        state[bucket] += 1
        _assert_envelope(resp, path="/leads")

    _run()
    print(f"\n[fuzz] /leads: {state}")
    assert state["5xx"] == 0, state


# --- GET /orchestrator/status/{job_id} ------------------------------------


def test_prod_orchestrator_status_envelope(client: httpx.Client) -> None:
    """Render's edge (Cloudflare) intercepts paths containing raw
    percent-escapes / control bytes / shell-meta and returns a non-JSON
    HTML 400 before FastAPI ever sees the request. That's an upstream
    hardening, not an app envelope bug — so we restrict the job_id
    alphabet to chars the edge passes through. The cursor-escape
    invariant on `/leads?cursor=...` (already pinned at
    `tests/security/test_cursor_escape.py`) covers the URL-decoding
    path; here we only care about the FastAPI handler's response
    shape for path-parameterised reads.
    """
    # Edge-safe alphabet: ASCII letters, digits, dash, underscore, dot.
    job_strategy = st.one_of(
        st.uuids().map(str),
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="-_.",
                blacklist_categories=("Cs",),
            ),
            max_size=128,
        ),
        st.sampled_from(["aaaa", "0", "a" * 200, "deadbeef-dead-beef-dead-beefdeadbeef"]),
    )
    state: dict[str, int] = {"sent": 0, "2xx": 0, "4xx": 0, "5xx": 0, "edge_html": 0}

    @_FUZZ_SETTINGS
    @given(job_strategy)
    def _run(job_id: str) -> None:
        time.sleep(_BASE_DELAY_S)
        safe = "".join(
            c for c in job_id
            if c.isalnum() or c in "-_."
        )
        if not safe:
            return
        resp = client.get(f"/orchestrator/status/{safe}")
        state["sent"] += 1
        bucket = (
            "2xx" if 200 <= resp.status_code < 300
            else "4xx" if 400 <= resp.status_code < 500
            else "5xx"
        )
        state[bucket] += 1
        # Edge-layer HTML 4xx (Cloudflare) doesn't violate our envelope
        # contract — record + continue. The contract test is on app-
        # layer responses, identified by JSON content-type.
        ctype = resp.headers.get("content-type", "")
        if 400 <= resp.status_code < 500 and "html" in ctype:
            state["edge_html"] += 1
            return
        _assert_envelope(resp, path="/orchestrator/status")

    _run()
    print(f"\n[fuzz] /orchestrator/status: {state}")
    assert state["5xx"] == 0, state
