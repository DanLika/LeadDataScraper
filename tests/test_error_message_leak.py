"""Error-response leakage sweep.

Trigger every recognisable error path and scrape the response (body +
headers) for information that shouldn't be there:

  - DB errors → no SQL fragments, no table names, no PostgREST codes
  - File errors → no filesystem paths
  - Gemini errors → no prompt body or model name echoed
  - Validation errors → field NAMES are fine (Pydantic exposes them by
    design), but the offending VALUE must be stringified + capped (the
    422 handler in `backend/main.py` does this — see
    `tests/test_json_pollution.py::TestLargeNumberPrecision`)
  - 500 responses → generic `{"error": "Internal server error"}` only
  - Headers → no `Server`, no `X-Powered-By`, no version banner
  - 401 / 403 / 404 distinguishable enough to be useful but not so
    distinct that an attacker enumerates the auth ladder via diff

Approach: monkey-patch the lazy `db`/`router`/`orchestrator` globals
with mocks that raise DB-shaped / file-shaped / Gemini-shaped errors,
hit the endpoints, and assert no sensitive substring leaks.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


API_KEY = "test-error-leak-key"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-tok-error-test")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from main import limiter

    try:
        limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:
        try:
            limiter.reset()
        except Exception:
            pass
    yield


def _h(extra: dict | None = None) -> dict[str, str]:
    h = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


# ---------------------------------------------------------------------------
# Sensitive substrings — none of these may appear in any response body
# or header value.
# ---------------------------------------------------------------------------

SENSITIVE_BODY_PATTERNS = [
    # Stack-trace fragments
    re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"File \"[^\"]+\", line \d+", re.IGNORECASE),
    # Python paths + module paths
    re.compile(r"/Users/[^\s\"']+", re.IGNORECASE),
    re.compile(r"/opt/homebrew/[^\s\"']+", re.IGNORECASE),
    re.compile(r"site-packages/[^\s\"']+", re.IGNORECASE),
    # SQL hints
    re.compile(r"SELECT\s+\*\s+FROM", re.IGNORECASE),
    re.compile(r"INSERT INTO", re.IGNORECASE),
    re.compile(r"UPDATE\s+\w+\s+SET", re.IGNORECASE),
    re.compile(r"DELETE FROM", re.IGNORECASE),
    re.compile(r"DROP TABLE", re.IGNORECASE),
    re.compile(r"PGRST\d{3}", re.IGNORECASE),  # PostgREST error codes
    re.compile(r"\bduplicate key value\b", re.IGNORECASE),
    re.compile(r"\brelation \".+\" does not exist\b", re.IGNORECASE),
    # Gemini / LLM internals
    re.compile(r"gemini-[a-z]+-(latest|flash|pro)", re.IGNORECASE),
    re.compile(r"google\.genai", re.IGNORECASE),
    re.compile(r"GenerateContentConfig", re.IGNORECASE),
    # Supabase URL / project ref
    re.compile(r"https?://[a-z0-9]+\.supabase\.co", re.IGNORECASE),
    # Secrets — these never leak in well-formed responses but we
    # canary against accidental echo.
    re.compile(rf"{re.escape(API_KEY)}"),
]

SENSITIVE_HEADERS = (
    "server",
    "x-powered-by",
    "x-aspnet-version",
    "x-runtime",
    "x-django-version",
    "x-rails",
    "via",
)


def _scan_body(body: str, path: str) -> list[str]:
    findings = []
    for pat in SENSITIVE_BODY_PATTERNS:
        m = pat.search(body)
        if m:
            findings.append(f"{path}: leaked {pat.pattern!r} → {m.group()!r}")
    return findings


def _scan_headers(headers, path: str) -> list[str]:
    findings = []
    for name in SENSITIVE_HEADERS:
        if name in {k.lower() for k in headers.keys()}:
            findings.append(f"{path}: leaked header {name} = {headers[name]!r}")
    return findings


# ---------------------------------------------------------------------------
# Fault-injection fixtures: rewire the lazy globals with mocks that
# raise specific exception shapes.
# ---------------------------------------------------------------------------


class _BoomDB:
    """Raises with sensitive substrings — verify these are NEVER echoed."""

    def __init__(self, message: str):
        self.client = MagicMock()
        # Common access patterns in the handlers
        self.client.table.side_effect = RuntimeError(message)
        self.client.rpc.side_effect = RuntimeError(message)

    def check_schema(self):
        return []


def _inject_lazy(name: str, value):
    import main as backend_main

    backend_main.__dict__.pop(name, None)
    setattr(backend_main, name, value)


def _restore_lazy(name: str):
    import main as backend_main

    backend_main.__dict__.pop(name, None)


# ---------------------------------------------------------------------------
# 1) DB error paths.
# ---------------------------------------------------------------------------

DB_FAULT_MESSAGES = [
    # PostgREST raw error shape
    'duplicate key value violates unique constraint "leads_pkey" '
    'DETAIL: Key (unique_key)=("abc") already exists.',
    # Filesystem path inside error
    "OSError(2, 'No such file or directory', '/etc/supabase/config.toml')",
    # Stack-trace-style
    "Traceback (most recent call last):\n  "
    'File "/opt/homebrew/lib/python3.14/site-packages/supabase/client.py", '
    'line 42, in execute\n  raise APIError("relation \\"leads\\" does not exist")',
    # SQL fragment
    "syntax error at or near \"SELECT * FROM leads WHERE unique_key='x';\"",
]


@pytest.mark.parametrize(
    "fault_msg",
    DB_FAULT_MESSAGES,
    ids=[f"db-fault-{i}" for i in range(len(DB_FAULT_MESSAGES))],
)
def test_db_fault_does_not_leak_internals(fault_msg):
    """Inject a DB exception with sensitive content; assert no fragment
    leaks into the response body."""
    boom = _BoomDB(fault_msg)
    _inject_lazy("db", boom)
    try:
        client = TestClient(app)
        # `/stats` is a cheap path that hits db.client.table().select().
        r = client.get("/stats", headers=_h())
        findings = _scan_body(r.text, "/stats")
        findings += _scan_headers(r.headers, "/stats")
        assert not findings, (
            f"DB-fault leak (msg={fault_msg[:50]!r}):\n  "
            + "\n  ".join(findings)
            + f"\n  Response: {r.text[:300]}"
        )
        # And the status must be a normal error code, not 200 with a
        # silent failure embedded.
        assert r.status_code >= 400, (
            f"DB fault returned 2xx — failure masked: {r.text[:200]}"
        )
    finally:
        _restore_lazy("db")


# ---------------------------------------------------------------------------
# 2) Gemini error paths.
# ---------------------------------------------------------------------------

GEMINI_FAULT_MESSAGES = [
    "google.genai.errors.APIError: 429 RESOURCE_EXHAUSTED — "
    "prompt: 'Find me dentists in Miami' rejected",
    "Failed to call gemini-flash-latest with "
    "GenerateContentConfig(system_instruction='Security rule:...'); "
    "see traceback below",
]


@pytest.mark.parametrize(
    "fault_msg",
    GEMINI_FAULT_MESSAGES,
    ids=[f"gemini-fault-{i}" for i in range(len(GEMINI_FAULT_MESSAGES))],
)
def test_gemini_fault_does_not_leak_prompt_or_model(fault_msg):
    """`/ask` and `/draft-outreach` route through the AgenticRouter. Inject
    a router exception with sensitive content; assert no fragment leaks."""
    router_mock = MagicMock()
    router_mock.route_instruction = AsyncMock(side_effect=RuntimeError(fault_msg))
    router_mock.execute_task = AsyncMock(side_effect=RuntimeError(fault_msg))
    _inject_lazy("router", router_mock)
    # Also need a benign db so the handler can reach the router call.
    db_mock = MagicMock()
    db_mock.client = MagicMock()
    _inject_lazy("db", db_mock)
    try:
        client = TestClient(app)
        r = client.post(
            "/ask",
            json={"instruction": {"text": "How many leads do I have?"}},
            headers=_h(),
        )
        findings = _scan_body(r.text, "/ask")
        findings += _scan_headers(r.headers, "/ask")
        assert not findings, (
            f"Gemini-fault leak (msg={fault_msg[:50]!r}):\n  "
            + "\n  ".join(findings)
            + f"\n  Response: {r.text[:300]}"
        )
    finally:
        _restore_lazy("router")
        _restore_lazy("db")


# ---------------------------------------------------------------------------
# 3) Validation errors — field names OK, raw values must not leak.
# ---------------------------------------------------------------------------


def test_validation_error_response_does_not_leak_raw_input():
    """Pydantic's `detail[].input` would normally echo the offending value
    — including potentially huge attacker-controlled blobs. The
    `_validation_with_authz_check` handler stringifies + caps at 512 chars.
    Verify the cap holds on a 10KB malicious value."""
    huge = "Z" * 10_000
    client = TestClient(app)
    r = client.post(
        "/process-lead",
        json={"unique_key": huge},
        headers=_h(),
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}"
    # Body must not contain the full 10K — capped at 512 chars in the
    # error response's `input` field.
    body = r.text
    assert "Z" * 600 not in body, (
        f"validation handler echoed the full 10K input — input cap missing"
    )


# ---------------------------------------------------------------------------
# 4) 500 responses — generic body only.
# ---------------------------------------------------------------------------


def test_uncaught_exception_returns_generic_500():
    """Force an uncaught exception through one of the handlers; the global
    `_json_exception_handler` must convert to `{"error": "Internal server
    error"}` — no stack trace, no exception class name."""
    leaky_msg = "super-secret leaky message with /Users/operator/.ssh/id_rsa contents"
    db_mock = MagicMock()
    db_mock.client = MagicMock()
    # `/stats` awaits `db.get_stats_rows()` — use AsyncMock so the
    # side_effect fires inside the async branch.
    db_mock.get_stats_rows = AsyncMock(side_effect=Exception(leaky_msg))
    _inject_lazy("db", db_mock)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/stats", headers=_h())
        if r.status_code == 500:
            body = r.json()
            # The body must be a plain `{"error": <generic>}` dict — no
            # nested 'detail' (stack trace), no exception class. Per-
            # handler error_response calls return their own static
            # strings ("Failed to fetch stats" etc.) which are fine.
            assert isinstance(body, dict) and set(body.keys()) <= {"error"}, (
                f"500 body shape leaks structure: {body}"
            )
            assert isinstance(body.get("error"), str), body
        # Whether the handler 500'd or recovered, the leaky message
        # must NEVER appear in the response.
        assert "id_rsa" not in r.text, (
            f"handler echoed sensitive substring: {r.text[:200]}"
        )
        assert "super-secret" not in r.text, (
            f"handler echoed leaky message: {r.text[:200]}"
        )
        assert "/Users/operator" not in r.text, (
            f"handler echoed filesystem path: {r.text[:200]}"
        )
    finally:
        _restore_lazy("db")


# ---------------------------------------------------------------------------
# 5) Headers — no `Server`, no `X-Powered-By`.
# ---------------------------------------------------------------------------


def test_no_server_or_powered_by_headers():
    """Across a representative sweep of endpoints, response headers must
    not include the standard fingerprint banners."""
    client = TestClient(app)
    paths = [
        ("GET", "/"),  # public liveness probe
        ("GET", "/stats"),  # authed read
        ("GET", "/leads"),  # authed read
        ("POST", "/process-lead"),  # validation 422
    ]
    leaks: list[str] = []
    for method, path in paths:
        r = client.request(method, path, headers=_h())
        leaks += _scan_headers(r.headers, path)
    assert not leaks, "Fingerprint headers found:\n  " + "\n  ".join(leaks)


# ---------------------------------------------------------------------------
# 6) Status-code distinguishability — 401 / 403 / 404 distinct, but not
#    so distinct that they enumerate the auth ladder.
# ---------------------------------------------------------------------------


def test_anon_404_and_403_are_distinguishable_but_dont_leak_route_shape():
    """Hit a non-existent route + a protected route, both without auth.
    They should return different statuses (404 vs 403) but the BODIES
    must NOT differ in a way that lets an attacker enumerate which
    routes exist when no key is presented."""
    client = TestClient(app)

    # No API key
    r_404 = client.get("/this-route-does-not-exist-xyz")
    r_403 = client.get("/leads")  # exists, requires X-API-Key
    r_403b = client.post("/process-lead")  # exists, requires X-API-Key

    assert r_404.status_code == 404
    assert r_403.status_code in (403, 422)
    assert r_403b.status_code in (403, 422)

    # The protected-route body must be generic (not "this is /leads endpoint").
    body_403 = r_403.text
    body_403b = r_403b.text
    for sensitive in ("/leads", "/process-lead", "unique_key", "LeadProcessRequest"):
        # Generic auth-failure message must not include the route name
        # or schema hints.
        if r_403.status_code == 403:
            assert sensitive not in body_403, (
                f"403 body leaked route detail {sensitive!r}: {body_403[:200]}"
            )
        if r_403b.status_code == 403:
            assert sensitive not in body_403b, (
                f"403 body leaked route detail {sensitive!r}: {body_403b[:200]}"
            )


def test_liveness_probe_does_not_leak_version_or_product():
    """The `/` endpoint is the unauthenticated liveness probe. It must
    return ONLY `{"status": "ok"}` — no product name, no version, no
    build info that gives an attacker free fingerprinting."""
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    # The only acceptable key is `status`.
    assert set(body.keys()) <= {"status"}, f"liveness probe leaked extra fields: {body}"
    assert body.get("status") == "ok", f"unexpected liveness body: {body}"
    # No product / version anywhere.
    for sensitive in ("LeadDataScraper", "version", "build", "git", "v1.", "v2."):
        assert sensitive.lower() not in r.text.lower(), (
            f"liveness probe leaked {sensitive!r}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 7) Docs disabled in default config.
# ---------------------------------------------------------------------------


def test_openapi_and_docs_disabled_by_default():
    """`/docs`, `/redoc`, `/openapi.json` enumerate every endpoint +
    every Pydantic model. Must 404 unless `ENABLE_DOCS=true`."""
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert r.status_code == 404, (
            f"{path} reachable without ENABLE_DOCS=true: {r.status_code}"
        )


# ---------------------------------------------------------------------------
# 8) Method-not-allowed body doesn't echo schema.
# ---------------------------------------------------------------------------


def test_method_not_allowed_response_does_not_leak_schema():
    """GET on a POST-only endpoint returns 405. The body must be the
    Starlette default (`{"detail": "Method Not Allowed"}` or similar)
    — never the Pydantic schema for that endpoint."""
    client = TestClient(app)
    r = client.get("/process-lead", headers=_h())
    assert r.status_code in (405, 404), r.status_code
    # Schema hints must not appear.
    body_lower = r.text.lower()
    for sensitive in ("unique_key", "leadprocessrequest", "constr"):
        assert sensitive not in body_lower, (
            f"405 body leaked schema {sensitive!r}: {r.text[:200]}"
        )
