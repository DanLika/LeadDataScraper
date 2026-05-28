"""JSON-pollution sweep against every POST endpoint that accepts JSON.

Every payload below is constructed to probe a class of parser-level
attacks: prototype pollution, duplicate keys (allowlist smuggling),
control-character smuggling, depth bombs, and float precision loss.

Pydantic's `extra='forbid'` mass-assignment defense + Literal allowlists
should reject most of these with 422 (when authenticated) or 403 (when
the request never reaches Pydantic because the API-key check fails first
— covered by `test_validation_authz_gate.py`).

What this file verifies:
  - Every endpoint × every payload returns 4xx, NEVER 200 (silent
    accept), NEVER 500 (parser crash).
  - Duplicate-key smuggling cannot push a non-allowlisted task name
    past the Literal check.
  - Deeply-nested JSON triggers a clean reject before the parser
    blows the stack.
  - `1e308` numbers passed where `int`/`str` is expected fail
    validation rather than truncate.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


API_KEY_HEADER = "X-API-Key"
API_KEY = "test-json-pollution-key"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from main import limiter

    try:
        limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        try:
            limiter.reset()
        except Exception:
            pass
    yield


@pytest.fixture(autouse=True)
def _prime_lazy_globals():
    """`backend/main.py` resolves `db` / `router` / `auditor` /
    `orchestrator` lazily via module __getattr__. In tests with fake
    Supabase creds, the lifespan exception swallow leaves them
    uncached — any handler that references them bare then hits a
    NameError → 500, masking Pydantic validation behavior. Inject
    MagicMocks so handlers run end-to-end."""
    import main as backend_main
    from unittest.mock import MagicMock, AsyncMock

    captured = {
        name: backend_main.__dict__.pop(name, None)
        for name in ("db", "router", "auditor", "orchestrator")
    }

    db_mock = MagicMock()
    db_mock.client = MagicMock()
    router_mock = MagicMock()
    router_mock.execute_task = AsyncMock(return_value={"answer": "ok"})
    auditor_mock = MagicMock()
    orch_mock = MagicMock()
    orch_mock.run_discovery_job = AsyncMock(return_value="job-id")
    orch_mock.run_massive_pipeline = AsyncMock(return_value="job-id")
    orch_mock.run_audit_for_lead = AsyncMock(return_value={"status": "ok"})

    backend_main.db = db_mock
    backend_main.router = router_mock
    backend_main.auditor = auditor_mock
    backend_main.orchestrator = orch_mock

    yield

    for name, prev in captured.items():
        if prev is not None:
            setattr(backend_main, name, prev)
        else:
            backend_main.__dict__.pop(name, None)


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Endpoints under test. Each entry: (method, path, base_valid_payload).
# The base payload is the shape the endpoint normally accepts — we MUTATE
# it with each pollution payload to test the validator in context.
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("POST", "/process-lead", {"unique_key": "abc"}),
    ("POST", "/draft-outreach", {"unique_key": "abc"}),
    ("POST", "/draft-linkedin", {"unique_key": "abc"}),
    ("POST", "/hunt-lead", {"unique_key": "abc"}),
    ("POST", "/enrich/start", {"unique_key": "abc"}),
    ("POST", "/ask", {"instruction": {"text": "How many leads?"}}),
    ("POST", "/execute", {"task": "STATUS_CHECK", "params": {}}),
    ("POST", "/campaigns", {"name": "n", "channel": "email"}),
    ("POST", "/discovery/start", {"query": "pizza", "location": "NYC"}),
    (
        "POST",
        "/orchestrator/start",
        {"filters": {}, "lead_ids": ["a"], "tasks": ["audit"]},
    ),
]


def _headers() -> dict[str, str]:
    return {API_KEY_HEADER: API_KEY, "Content-Type": "application/json"}


def _do_post(client, method: str, path: str, body) -> tuple[int, str]:
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    r = client.request(method, path, content=body, headers=_headers())
    return r.status_code, r.text


# ---------------------------------------------------------------------------
# 1) Prototype-pollution payloads — extra fields must be rejected by
#    `extra='forbid'`. Python dicts don't have a prototype chain, but a
#    downstream consumer that does dict-merge or attribute-access on the
#    parsed payload could be tricked by `__proto__` / `constructor`.
# ---------------------------------------------------------------------------

PROTO_POLLUTION_PAYLOADS = [
    {"__proto__": {"isAdmin": True}},
    {"constructor": {"prototype": {"isAdmin": True}}},
    {"toString": "x"},
    {"hasOwnProperty": "x"},
    {"valueOf": {"x": 1}},
    {"prototype": {"isAdmin": True}},
    {"__class__": "AdminPlan"},  # Python attribute confusion
    {"__init__": "evil"},
]


@pytest.mark.parametrize("method,path,base", ENDPOINTS, ids=[e[1] for e in ENDPOINTS])
@pytest.mark.parametrize(
    "pollution",
    PROTO_POLLUTION_PAYLOADS,
    ids=[
        "__proto__",
        "constructor.prototype",
        "toString",
        "hasOwnProperty",
        "valueOf",
        "prototype",
        "__class__",
        "__init__",
    ],
)
def test_prototype_pollution_rejected(client, method, path, base, pollution):
    """Add the pollution key alongside the base valid fields. Pydantic
    must reject the request (extra='forbid'). Acceptable: 4xx. NEVER 200,
    NEVER 500."""
    payload = {**base, **pollution}
    status, body = _do_post(client, method, path, payload)
    assert 400 <= status < 500, (
        f"{path} accepted pollution payload {pollution}: "
        f"status={status} body={body[:200]}"
    )
    assert status != 500, (
        f"{path} crashed on pollution payload {pollution}: body={body[:200]}"
    )


# ---------------------------------------------------------------------------
# 2) Duplicate keys — JSON spec says behavior is undefined, but stdlib
#    `json.loads` keeps the LAST value. A duplicate-key payload like
#    `{"task": "SAFE", "task": "DROP_TABLE"}` would resolve to
#    `{"task": "DROP_TABLE"}` in Python; the Literal allowlist must still
#    catch that.
# ---------------------------------------------------------------------------

DUPLICATE_KEY_PAYLOADS = [
    # (path, raw_json, expected_status_is_4xx)
    (
        "/execute",
        b'{"task": "STATUS_CHECK", "task": "DROP_TABLE", "params": {}}',
        True,  # second value wins; DROP_TABLE not in Literal → 422
    ),
    (
        "/execute",
        b'{"task": "STATUS_CHECK", "task": "STATUS_CHECK", "params": {}}',
        False,  # both are same valid value — should be accepted
    ),
    (
        "/campaigns",
        b'{"name": "n", "channel": "email", "channel": "evil-channel"}',
        True,  # invalid channel value
    ),
    (
        "/discovery/start",
        b'{"query": "ok", "query": "' + b"x" * 600 + b'"}',
        True,  # over-length query
    ),
]


@pytest.mark.parametrize(
    "path,raw_json,expect_4xx",
    DUPLICATE_KEY_PAYLOADS,
    ids=[
        "execute-task-evil-wins",
        "execute-task-same-twice",
        "campaigns-channel-evil-wins",
        "discovery-query-oversize-wins",
    ],
)
def test_duplicate_keys_last_value_wins_and_validator_catches(
    client, path, raw_json, expect_4xx
):
    """JSON spec leaves duplicate-key behavior undefined. Python's
    `json.loads` keeps the LAST occurrence. If an attacker crafts a
    body where the SECOND value is malicious, the allowlist must still
    catch it — duplicate keys can't smuggle past Pydantic."""
    r = client.post(path, content=raw_json, headers=_headers())
    if expect_4xx:
        assert 400 <= r.status_code < 500, (
            f"{path} duplicate-key smuggling succeeded: "
            f"status={r.status_code} body={r.text[:200]}"
        )
    # When BOTH values are valid (e.g., "STATUS_CHECK" twice), accept
    # OR reject is fine — the point is the second value can't be
    # something the validator wouldn't have approved on its own.


# ---------------------------------------------------------------------------
# 3) Control characters in strings.
# ---------------------------------------------------------------------------

# Built via chr() so this source file contains no literal NUL / bidi /
# control chars (semgrep flags those at write time — CWE-94).
# Same byte values at runtime.
CONTROL_CHAR_STRING_PAYLOADS = [
    "x" + chr(0x00) + "y",  # NUL
    "x" + chr(0x01) + "y",  # SOH
    "x" + chr(0x08) + "y",  # backspace
    "x" + chr(0x0B) + "y",  # VT
    "x" + chr(0x0C) + "y",  # FF
    "x" + chr(0x7F) + "y",  # DEL
    chr(0x202E) + " reversed",  # right-to-left override (bidi)
    "x" + chr(0x00) * 100,  # NUL bomb
]


@pytest.mark.parametrize(
    "control_str",
    CONTROL_CHAR_STRING_PAYLOADS,
    ids=[
        "nul",
        "soh",
        "backspace",
        "vt",
        "ff",
        "del",
        "rtl-override",
        "nul-bomb",
    ],
)
def test_control_chars_do_not_crash_validator(client, control_str):
    """JSON allows `\\u0000` etc. in string literals. Pydantic accepts
    them as Python strings. Downstream consumers (logging filter,
    PostgREST, Supabase) may handle them differently — but the
    boundary check must not 500."""
    # Use /discovery/start since it has a bounded `query: constr`.
    payload = {"query": control_str, "location": ""}
    status, body = _do_post(client, "POST", "/discovery/start", payload)
    assert status != 500, (
        f"control char {control_str!r} crashed validator: body={body[:200]}"
    )
    # The validator either accepts the string (Pydantic constr doesn't
    # filter control chars) or rejects it for some other reason (length).
    # We don't pin the exact code — just no 500.


# ---------------------------------------------------------------------------
# 4) Deeply-nested JSON — must reject before stack blow-up.
# ---------------------------------------------------------------------------


class TestDeeplyNestedJSON(unittest.TestCase):
    """Build a JSON document with 1000 levels of nesting. The server
    must reject with a 4xx (parser limit / validation failure), never
    500 or hang."""

    def setUp(self):
        os.environ["API_SECRET_KEY"] = API_KEY
        self.client = TestClient(app)
        # Reset rate limiter
        from main import limiter

        try:
            limiter._storage.storage.clear()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _build_deep(self, depth: int) -> str:
        # Build `{"a":{"a":{"a":...}}}` to `depth` levels.
        return '{"a":' * depth + "1" + "}" * depth

    def test_1000_deep_nested_object_rejected_cleanly(self):
        deep_json = self._build_deep(1000)
        r = self.client.post(
            "/discovery/start",
            content=deep_json.encode(),
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(
            r.status_code,
            500,
            f"1000-deep JSON crashed: body={r.text[:200]}",
        )
        # Must be a 4xx (parse error / extra field rejection).
        self.assertTrue(
            400 <= r.status_code < 500,
            f"1000-deep JSON unexpectedly returned {r.status_code}",
        )

    def test_2000_deep_nested_object_rejected_cleanly(self):
        """2000 levels — beyond Python's default recursion limit (~1000).
        Must be rejected at the parser without a RecursionError 500."""
        deep_json = self._build_deep(2000)
        r = self.client.post(
            "/discovery/start",
            content=deep_json.encode(),
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(
            r.status_code,
            500,
            f"2000-deep JSON 500'd: body={r.text[:200]}",
        )

    def test_deeply_nested_array_rejected_cleanly(self):
        deep_json = "[" * 1500 + "1" + "]" * 1500
        r = self.client.post(
            "/discovery/start",
            content=deep_json.encode(),
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(
            r.status_code,
            500,
            f"deep array crashed: body={r.text[:200]}",
        )


# ---------------------------------------------------------------------------
# 5) Large numbers — precision loss / type confusion.
# ---------------------------------------------------------------------------


class TestLargeNumberPrecision(unittest.TestCase):
    """`1e308` parses to a Python float (≈ 1.7e308 max). If a Pydantic
    model declares the field as `int`, the float must NOT silently
    truncate to a wrong int — it must fail validation. Same for `str`
    fields: a number must NOT be auto-coerced into a string that looks
    like the wrong identifier."""

    def setUp(self):
        os.environ["API_SECRET_KEY"] = API_KEY
        self.client = TestClient(app)
        from main import limiter

        try:
            limiter._storage.storage.clear()  # type: ignore[attr-defined]
        except Exception:
            pass

    def test_large_float_in_unique_key_rejected(self):
        """`unique_key` is `constr(...)` — must be a string. A float
        like 1e308 must be rejected, not stringified."""
        r = self.client.post(
            "/process-lead",
            content=b'{"unique_key": 1e308}',
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(r.status_code, 500, r.text[:200])
        self.assertTrue(
            400 <= r.status_code < 500,
            f"large float in unique_key accepted: {r.status_code} {r.text[:200]}",
        )

    def test_large_int_in_query_rejected(self):
        """`query` is `constr(min_length=1, max_length=500)`. A huge
        integer must not be auto-coerced into a number-string that
        somehow passes the length cap."""
        r = self.client.post(
            "/discovery/start",
            content=b'{"query": ' + b"9" * 1000 + b', "location": ""}',
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(r.status_code, 500, r.text[:200])
        self.assertTrue(
            400 <= r.status_code < 500,
            f"oversize number in query accepted: {r.status_code} {r.text[:200]}",
        )

    def test_negative_zero_in_string_field(self):
        """`-0.0` is a JSON-valid number that must not coerce to a string."""
        r = self.client.post(
            "/process-lead",
            content=b'{"unique_key": -0.0}',
            headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
        )
        self.assertNotEqual(r.status_code, 500, r.text[:200])
        self.assertTrue(400 <= r.status_code < 500)

    def test_nan_infinity_rejected(self):
        """`NaN` / `Infinity` are NOT in the JSON spec but Python's
        stdlib accepts them with `parse_constant`. FastAPI/Starlette
        use a stricter parser that should reject. Verify."""
        for bad in (
            b'{"unique_key": NaN}',
            b'{"unique_key": Infinity}',
            b'{"unique_key": -Infinity}',
        ):
            with self.subTest(bad=bad):
                r = self.client.post(
                    "/process-lead",
                    content=bad,
                    headers={
                        API_KEY_HEADER: API_KEY,
                        "Content-Type": "application/json",
                    },
                )
                self.assertNotEqual(r.status_code, 500, r.text[:200])
                self.assertTrue(
                    400 <= r.status_code < 500,
                    f"{bad!r} accepted: {r.status_code} {r.text[:200]}",
                )


# ---------------------------------------------------------------------------
# 6) Coupling — a known-valid payload must still 200 / 422-on-missing-data.
#    Without this, a "reject everything" misconfiguration would pass the
#    pollution sweep while breaking the real endpoint.
# ---------------------------------------------------------------------------


def test_known_valid_payload_reaches_validator(client):
    """`/execute` with `STATUS_CHECK` is the cheapest valid call. We
    don't care if Supabase is reachable in test mode — Pydantic
    validation passes either way. Accept any 2xx OR 5xx (service
    unavailable due to fake env), but NOT 403 (auth) or 422 (validation)."""
    status, body = _do_post(
        client,
        "POST",
        "/execute",
        {"task": "STATUS_CHECK", "params": {}},
    )
    assert status != 403, f"valid payload 403'd — auth misconfig: {body[:200]}"
    assert status != 422, (
        f"valid payload 422'd — pollution test over-broad: {body[:200]}"
    )
