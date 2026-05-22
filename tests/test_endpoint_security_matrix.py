"""Endpoint security matrix — auth / validation / rate-limit contract for
every route in `backend/main.py`.

Drives a real ASGI app via `httpx.AsyncClient` + `ASGITransport` (no
network, no lifespan — so `_assert_single_tenant_if_enforced` and the
Supabase startup probe never fire; `db.client` is `None`, which is fine
because every assertion here lands on a layer that runs *before* the
handler body).

IMPORTANT — 401 vs 403
----------------------
The task spec says auth failures "expect 401". The app actually returns
**403** (`verify_api_key` / `verify_admin_token` in `backend/main.py`,
and the `_validation_with_authz_check` 422-gate). 403 is the canonical,
already-locked behaviour — see `tests/test_validation_authz_gate.py`.
These tests assert the **real** status (403). They would catch a
regression to 401 just as well as one to 200.

Coverage per category
---------------------
* missing X-API-Key            -> 403   (all 31 authed routes)
* wrong   X-API-Key            -> 403   (all 31 authed routes)
* constant-time key compare    -> source asserts `secrets.compare_digest`
* X-Admin-Token gate           -> 403   (`DELETE /leads/clear`)
* empty body, body required    -> 403 anon / 422 authed
* extra fields (extra=forbid)  -> 422   (every JSON-body model)
* max-length boundary (+1)     -> 422   (every bounded `constr` field)
* unicode / NUL / zero-width   -> NOT 422 (boundary is length-only;
                                  content sanitisation is downstream)
* rate-limit boundary (N+1)    -> 429   (curated db-guarded low-cap subset)

The rate-limit category is deliberately *curated*, not exhaustive:
exercising N+1 against a 60/minute route means 61 handler invocations,
and most handlers do real work (orchestrator jobs, exports). The two
chosen routes both short-circuit on `if not db.client` so the first N
requests are cheap 503s. Every other route's `@limiter.limit` decorator
is verified structurally by `test_every_authed_route_declares_a_limit`.
"""
import asyncio
import inspect
import os
import sys

import httpx
import pytest

backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402
import main as main_mod  # noqa: E402


API_KEY = "matrix-suite-api-key"
ADMIN_TOKEN = "matrix-suite-admin-token"

# A placeholder UUID for `{campaign_id}` / `{job_id}` path params. The
# handlers 404/503 on it, but auth + validation + rate-limit all fire
# before the row is ever looked up.
PID = "00000000-0000-0000-0000-000000000000"

# café · NUL · ZWSP · ZWJ · BOM · pile-of-poo · CJK — 12 codepoints, well
# under every `constr` limit in the app (smallest is 64).
UNICODE_STR = "caf\u00e9 \u0000\u200b\u200d\ufeff\U0001f4a9\u65e5\u672c\u8a9e"


async def _astub_dict(*_a, **_k):
    return {"stub": True}


async def _astub_unknown(*_a, **_k):
    # /ask treats task=UNKNOWN as a plain-text reply — keeps the route
    # off the execute_task / Gemini path.
    return {"task": "UNKNOWN", "raw": "stub"}


async def _astub_jobid(*_a, **_k):
    return "stub-job-id"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Make every request hermetic. Two reasons:

    1. `backend/main.py` runs `load_dotenv()` at import — on a dev machine
       the real Supabase + Gemini creds load, so an un-stubbed handler
       would mutate the production DB and bill real Gemini calls. (The
       first draft of this file did exactly that.)
    2. The auth / validation / rate-limit layers under test all run
       *before* the handler body, so nulling the DB client and stubbing
       the AI router / orchestrator changes nothing we assert on — it
       only removes side effects.

    Secrets are read at request time, so setting them post-import works."""
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    # No DB: every db-guarded handler short-circuits to 503; no inserts.
    monkeypatch.setattr(main_mod.db, "client", None, raising=False)
    # No Gemini, no Playwright, no orchestrator jobs.
    monkeypatch.setattr(main_mod.router, "route_instruction", _astub_unknown, raising=False)
    monkeypatch.setattr(main_mod.router, "execute_task", _astub_dict, raising=False)
    monkeypatch.setattr(main_mod.orchestrator, "run_massive_pipeline", _astub_jobid, raising=False)
    monkeypatch.setattr(main_mod.orchestrator, "run_discovery_job", _astub_jobid, raising=False)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """slowapi's MemoryStorage is process-global. Clear it before every
    test so a valid-key request in one test can't consume another test's
    (or another file's) rate-limit budget."""
    _clear_limiter()
    yield
    _clear_limiter()


def _clear_limiter():
    lim = app.state.limiter
    for storage in (
        getattr(lim, "_storage", None),
        getattr(getattr(lim, "_limiter", None), "storage", None),
    ):
        if storage is not None:
            try:
                storage.reset()
            except Exception:  # pragma: no cover - storage backend variance
                pass


# ──────────────────────────── request helper ─────────────────────────

def _do(method, path, *, api_key=None, admin_token=None, **kw):
    """Run one request against the in-process ASGI app and return the
    httpx.Response. Sync wrapper so tests stay plainly parametrizable."""
    headers = dict(kw.pop("headers", {}))
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if admin_token is not None:
        headers["X-Admin-Token"] = admin_token

    async def _run():
        # raise_app_exceptions=False — an exception that escapes the app
        # (handler bug) should surface as the 500 *response* we assert on,
        # not propagate out of httpx and abort the test.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.request(method, path, headers=headers, **kw)

    return asyncio.run(_run())


# ─────────────────────────── endpoint tables ─────────────────────────

# (method, path, requires_admin) — every route except the public `/`.
AUTHED_ENDPOINTS = [
    ("GET", "/leads", False),
    ("GET", "/audit-status", False),
    ("GET", "/health/schema", False),
    ("GET", "/insights", False),
    ("GET", "/stats", False),
    ("GET", f"/orchestrator/status/{PID}", False),
    ("GET", "/export", False),
    ("GET", "/export/download", False),
    ("GET", "/export/outreach", False),
    ("GET", "/campaigns", False),
    ("GET", f"/campaigns/{PID}", False),
    ("GET", f"/campaigns/{PID}/export", False),
    ("POST", "/process-all", False),
    ("POST", "/audit/stop", False),
    ("POST", "/hunt-all", False),
    ("POST", f"/campaigns/{PID}/generate", False),
    ("POST", f"/campaigns/{PID}/start", False),
    ("POST", f"/campaigns/{PID}/pause", False),
    ("POST", f"/orchestrator/stop/{PID}", False),
    ("POST", "/process-lead", False),
    ("POST", "/ask", False),
    ("POST", "/draft-outreach", False),
    ("POST", "/draft-linkedin", False),
    ("POST", "/execute", False),
    ("POST", "/hunt-lead", False),
    ("POST", "/discovery/start", False),
    ("POST", "/enrich/start", False),
    ("POST", "/orchestrator/start", False),
    ("POST", "/campaigns", False),
    ("POST", "/upload", False),
    ("DELETE", "/leads/clear", True),
]


def _lead_body_endpoint(path):
    """LeadProcessRequest is reused by 5 routes — one record builder."""
    name = path.strip("/").replace("/", "-")
    return {
        "id": name,
        "path": path,
        "valid": {"unique_key": "lead-key-1"},
        "extra": {"unique_key": "lead-key-1", "injected_field": "x"},
        # (label, over-limit body) — each must 422.
        "maxlen": [(f"{name}.unique_key", {"unique_key": "x" * 129})],
        # unicode in every string field, all under the length cap.
        "unicode": {"unique_key": UNICODE_STR},
    }


# Every route that takes a JSON body model. Used by the empty-body,
# extra-fields, max-length and unicode categories.
BODY_ENDPOINTS = [
    _lead_body_endpoint("/process-lead"),
    _lead_body_endpoint("/draft-outreach"),
    _lead_body_endpoint("/draft-linkedin"),
    _lead_body_endpoint("/hunt-lead"),
    _lead_body_endpoint("/enrich/start"),
    {
        "id": "ask",
        "path": "/ask",
        "valid": {"instruction": {"text": "how many leads"}},
        "extra": {"instruction": {"text": "hi"}, "injected_field": "x"},
        "maxlen": [("ask.instruction.text", {"instruction": {"text": "x" * 4001}})],
        "unicode": {"instruction": {"text": UNICODE_STR}},
    },
    {
        "id": "execute",
        "path": "/execute",
        "valid": {"task": "STATUS_CHECK"},
        "extra": {"task": "STATUS_CHECK", "injected_field": "x"},
        "maxlen": [
            ("execute.params.unique_key",
             {"task": "STATUS_CHECK", "params": {"unique_key": "x" * 129}}),
            ("execute.params.query",
             {"task": "STATUS_CHECK", "params": {"query": "x" * 501}}),
            ("execute.params.filters",
             {"task": "STATUS_CHECK", "params": {"filters": "x" * 65}}),
        ],
        "unicode": {"task": "STATUS_CHECK", "params": {"query_text": UNICODE_STR}},
    },
    {
        "id": "discovery-start",
        "path": "/discovery/start",
        "valid": {"query": "dentists in Mostar"},
        "extra": {"query": "dentists", "injected_field": "x"},
        "maxlen": [
            ("discovery.query", {"query": "x" * 501}),
            ("discovery.location", {"query": "ok", "location": "x" * 201}),
        ],
        "unicode": {"query": UNICODE_STR, "location": UNICODE_STR},
    },
    {
        "id": "orchestrator-start",
        "path": "/orchestrator/start",
        # PipelineRequest has no required fields — {} is a valid body.
        "valid": {},
        "extra": {"injected_field": "x"},
        "maxlen": [
            ("orchestrator.lead_ids[item]", {"lead_ids": ["x" * 129]}),
            ("orchestrator.tasks[item]", {"tasks": ["x" * 65]}),
        ],
        "unicode": {"lead_ids": [UNICODE_STR], "tasks": [UNICODE_STR]},
    },
    {
        "id": "campaigns",
        "path": "/campaigns",
        "valid": {"name": "Spring Push", "channel": "email"},
        "extra": {"name": "C", "channel": "email", "injected_field": "x"},
        "maxlen": [
            ("campaigns.name", {"name": "x" * 201, "channel": "email"}),
            ("campaigns.segment_filter",
             {"name": "C", "channel": "email", "segment_filter": "x" * 201}),
        ],
        "unicode": {"name": UNICODE_STR, "channel": "email",
                    "segment_filter": UNICODE_STR},
    },
]

_BODY_IDS = [e["id"] for e in BODY_ENDPOINTS]


# ───────────────────────── A. missing X-API-Key ──────────────────────

@pytest.mark.parametrize("method,path,_admin", AUTHED_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, _ in AUTHED_ENDPOINTS])
def test_missing_api_key_returns_403(method, path, _admin):
    """No `X-API-Key` header — every authed route rejects with 403 and the
    generic message. (Spec said 401; app returns 403 — see module docstring.)"""
    res = _do(method, path)
    assert res.status_code == 403, f"{method} {path} -> {res.status_code}"
    assert res.json() == {"detail": "Invalid or missing API key"}


# ───────────────────────── B. wrong X-API-Key ────────────────────────

@pytest.mark.parametrize("method,path,_admin", AUTHED_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, _ in AUTHED_ENDPOINTS])
def test_wrong_api_key_returns_403(method, path, _admin):
    """A non-matching key is rejected exactly like a missing one — no
    distinguishing status, body, or message."""
    res = _do(method, path, api_key="definitely-not-the-key")
    assert res.status_code == 403, f"{method} {path} -> {res.status_code}"
    assert res.json() == {"detail": "Invalid or missing API key"}


def test_api_key_compare_is_constant_time():
    """A timing test would be flaky on a JIT'd interpreter under load. The
    durable assertion is that the comparison goes through
    `secrets.compare_digest` rather than `==` (which short-circuits on the
    first differing byte and leaks key length/prefix via timing)."""
    for fn in (main_mod.verify_api_key, main_mod.verify_admin_token):
        src = inspect.getsource(fn)
        assert "secrets.compare_digest" in src, f"{fn.__name__} must use compare_digest"
        assert "==" not in src.split("compare_digest")[0].split("def ")[1], (
            f"{fn.__name__} appears to compare the secret with == before compare_digest"
        )
    # The rate-limit key derivation trusts XFF only behind a key check —
    # that compare must also be constant-time.
    assert "secrets.compare_digest" in inspect.getsource(main_mod._rate_limit_key)


# ─────────────────── C. X-Admin-Token gate (/leads/clear) ────────────

def test_clear_leads_valid_api_key_but_missing_admin_token_403():
    """`DELETE /leads/clear` carries a second `verify_admin_token`
    dependency. A valid API key alone must not be enough."""
    res = _do("DELETE", "/leads/clear", api_key=API_KEY)
    assert res.status_code == 403
    assert res.json() == {"detail": "Invalid or missing admin token"}


def test_clear_leads_wrong_admin_token_403():
    res = _do("DELETE", "/leads/clear", api_key=API_KEY, admin_token="wrong-admin")
    assert res.status_code == 403
    assert res.json() == {"detail": "Invalid or missing admin token"}


# ───────────────────── D. empty body where body required ─────────────

@pytest.mark.parametrize("ep", BODY_ENDPOINTS, ids=_BODY_IDS)
def test_empty_body_403_anon_then_422_authed(ep):
    """An empty request body on a route with a required model. Anonymous
    callers get 403 (the 422-gate hides the schema); authed callers get the
    real 422. Order matters: the schema shape must never leak pre-auth."""
    anon = _do("POST", ep["path"], content=b"")
    assert anon.status_code == 403, f'{ep["path"]} anon -> {anon.status_code}'
    assert anon.json() == {"detail": "Invalid or missing API key"}

    authed = _do("POST", ep["path"], api_key=API_KEY, content=b"")
    assert authed.status_code == 422, f'{ep["path"]} authed -> {authed.status_code}'
    # Authed 422 carries the structured Pydantic detail array.
    assert isinstance(authed.json().get("detail"), list)


# ─────────────────── E. extra fields (extra='forbid') ────────────────

@pytest.mark.parametrize("ep", BODY_ENDPOINTS, ids=_BODY_IDS)
def test_extra_field_rejected_422(ep):
    """Every inbound model pins `extra='forbid'` (mass-assignment defense).
    A body that is otherwise valid but carries one unknown key must 422 —
    proving the unknown key is rejected, not silently dropped."""
    res = _do("POST", ep["path"], api_key=API_KEY, json=ep["extra"])
    assert res.status_code == 422, f'{ep["path"]} -> {res.status_code}'
    detail = res.json()["detail"]
    # The rejection must specifically name the forbidden extra input.
    assert any(d.get("type") == "extra_forbidden" for d in detail), detail


# ──────────────────── F. max-length boundary (+1) ────────────────────

_MAXLEN_CASES = [
    (ep["path"], label, body)
    for ep in BODY_ENDPOINTS
    for (label, body) in ep["maxlen"]
]


@pytest.mark.parametrize("path,label,body", _MAXLEN_CASES,
                         ids=[c[1] for c in _MAXLEN_CASES])
def test_one_char_over_constr_limit_rejected_422(path, label, body):
    """Each bounded `constr` field, sent at exactly limit+1 characters,
    must 422 — the pre-handler memory-DoS bound is real, not advisory."""
    res = _do("POST", path, api_key=API_KEY, json=body)
    assert res.status_code == 422, f"{label} (limit+1) -> {res.status_code}"
    detail = res.json()["detail"]
    assert any("too_long" in d.get("type", "") for d in detail), detail


# ───────────── G. unicode / NUL / zero-width are length-only ──────────

@pytest.mark.parametrize("ep", BODY_ENDPOINTS, ids=_BODY_IDS)
def test_unicode_nul_zerowidth_pass_validation(ep):
    """A string carrying emoji, a NUL byte, ZWSP/ZWJ and a BOM — but under
    the length cap — must NOT 422. This locks in that the API boundary
    bounds *length only*; content sanitisation happens downstream (CSV
    formula guard, `<UNTRUSTED_DATA>` prompt fencing). If someone later
    adds a Pydantic `pattern=` content rule, this test flags the change so
    the downstream guards can be re-reviewed."""
    res = _do("POST", ep["path"], api_key=API_KEY, json=ep["unicode"])
    assert res.status_code != 422, (
        f'{ep["path"]} rejected unicode body with 422 — boundary is no '
        f"longer length-only: {res.text[:300]}"
    )
    # Valid key — must never be an auth rejection either.
    assert res.status_code != 403


# ──────────────────── H. rate-limit boundary (N+1) ───────────────────

# Curated: both routes hit `if not db.client: return 503` immediately, so
# the first N requests are cheap and side-effect-free. (limit, body)
_RATE_LIMIT_CASES = [
    ("/orchestrator/start", 3, {}),          # @limiter.limit("3/minute")
    (f"/campaigns/{PID}/generate", 3, None),  # @limiter.limit("3/minute")
]


@pytest.mark.parametrize("path,limit,body", _RATE_LIMIT_CASES,
                         ids=[c[0] for c in _RATE_LIMIT_CASES])
def test_rate_limit_boundary_returns_429(path, limit, body):
    """The first `limit` authed requests within the window pass the limiter;
    request `limit + 1` is rejected with 429. The autouse fixture clears the
    limiter storage first, so the count starts at zero."""
    kw = {"json": body} if body is not None else {}
    for i in range(limit):
        res = _do("POST", path, api_key=API_KEY, **kw)
        assert res.status_code != 429, f"req {i + 1}/{limit} hit 429 early"

    over = _do("POST", path, api_key=API_KEY, **kw)
    assert over.status_code == 429, f"req {limit + 1} -> {over.status_code} (expected 429)"


def test_every_authed_route_declares_a_limit():
    """Structural check standing in for an exhaustive per-route 429 test:
    every authed route's handler must carry slowapi's `@limiter.limit`
    decorator. slowapi marks decorated callables via `__wrapped__` and the
    `_rate_limit` marker attribute. A new endpoint added without a limit
    fails here."""
    unlimited = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in (None, "/") or not getattr(route, "methods", None):
            continue
        if {"HEAD", "OPTIONS"} >= set(route.methods):
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        # slowapi wraps the handler; the original is reachable via closure.
        src = inspect.getsource(endpoint) if hasattr(endpoint, "__wrapped__") else ""
        marked = hasattr(endpoint, "__wrapped__") or "limiter.limit" in src
        if not marked:
            unlimited.append(f"{sorted(route.methods)} {path}")
    assert not unlimited, f"routes missing @limiter.limit: {unlimited}"


# ───────────────────────── sanity: public root ───────────────────────

def test_root_is_public_and_metadata_free():
    """`/` is the only unauthenticated route. It must answer 200 with no
    key and expose no product/version fingerprint."""
    res = _do("GET", "/")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
