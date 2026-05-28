"""
Endpoint-hardening battery for backend/main.py.

Covers all 32 authed endpoints (the `/` liveness probe is the only
unauthenticated route — exempted) under seven concerns from the brief:

  1. Missing X-API-Key                 -> 403 (auth-not-configured / invalid)
  2. Wrong X-API-Key                   -> 403 (constant-time compare)
  3. Empty body where body required    -> 422 (only AFTER auth passes)
  4. Extra fields in body              -> 422 (extra='forbid' working)
  5. Max-length boundary (limit + 1)   -> 422 (constr() enforced)
  6. Adversarial strings in fields     -> no 500 (server doesn't crash on
                                           NULs, zero-width, RTL override,
                                           emoji, embedded JSON)
  7. Rate-limit boundary (N+1 / window) -> 429 (slowapi key derivation works)

IMPORTANT — actual auth status code is 403, not 401.
`backend/main.py:88-95` raises HTTPException(403) on both missing AND
wrong key. The brief asked for 401; the test asserts what the code
actually does (403). If we want 401 instead, change verify_api_key
intentionally — don't drift the test to mask the discrepancy.

Heavy module-level singletons (db, router, auditor, orchestrator) are
replaced with MagicMocks AFTER importing backend.main but BEFORE any
request flows. Handlers that touch DB/AI return 500/503 paths gracefully
in the production code; for tests that only check 403 / 422 / 429 the
deeper layers don't execute (validation/auth gate fires first).

No live Gemini, no live Supabase, no API key needed at test time.
"""

import asyncio
import importlib
import os
import sys
import unittest
from contextlib import asynccontextmanager
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from httpx import ASGITransport

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_KEY = "test-api-key-correct"
ADMIN_TOKEN = "test-admin-token-correct"


# ---- App builder ------------------------------------------------------------


def _install_singleton_mocks(backend_main_module) -> None:
    """
    Replace the lazy singletons in backend.main with mocks. Setting them in
    globals() short-circuits the module __getattr__ fallback.
    """
    mock_db = MagicMock(name="mock_db")
    mock_db.client = None  # most handlers branch on this and return 503 cleanly
    backend_main_module.db = mock_db

    mock_router = MagicMock(name="mock_router")
    mock_router.execute_task = AsyncMock(return_value={"message": "ok"})
    mock_router.route_instruction = AsyncMock(
        return_value={
            "task": "UNKNOWN",
            "params": {},
            "reasoning": "mocked",
            "raw": "ok",
        }
    )
    backend_main_module.router = mock_router

    mock_auditor = MagicMock(name="mock_auditor")
    mock_auditor.audit_all_pending_async = AsyncMock(return_value={"message": "ok"})
    mock_auditor.stop = MagicMock()
    backend_main_module.auditor = mock_auditor

    mock_orchestrator = MagicMock(name="mock_orchestrator")
    mock_orchestrator.run_massive_pipeline = AsyncMock(return_value="job-test-1")
    mock_orchestrator.run_discovery_job = AsyncMock(return_value="job-disc-1")
    mock_orchestrator.run_enrichment_job = AsyncMock(return_value="job-enrich-1")
    mock_orchestrator.run_hunt_all_job = AsyncMock(return_value="job-hunt-1")
    mock_orchestrator.run_audit_job = AsyncMock(return_value="job-audit-1")
    mock_orchestrator.get_job_status = MagicMock(return_value={"status": "completed"})
    mock_orchestrator.get_active_job = MagicMock(return_value=None)
    mock_orchestrator.stop_job = MagicMock(return_value={"ok": True})
    backend_main_module.orchestrator = mock_orchestrator


def _fresh_app():
    """
    Re-import backend.main with env vars and mocked singletons in place.
    Re-import is required so slowapi's in-memory rate-limit storage starts
    empty for each test class (otherwise tests cross-contaminate).
    """
    # Wipe any prior cached module so slowapi state resets.
    for mod_name in list(sys.modules):
        if mod_name == "backend.main" or mod_name == "backend":
            del sys.modules[mod_name]

    os.environ["API_SECRET_KEY"] = API_KEY
    os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
    os.environ.pop("OPERATOR_EMAIL", None)  # disable boot-time tenancy check
    os.environ.setdefault("ALLOWED_ORIGINS", "http://test")
    os.environ.setdefault("GEMINI_API_KEY", "fake-key-not-used")

    from backend import main as backend_main

    _install_singleton_mocks(backend_main)
    return backend_main.app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---- Endpoint inventory -----------------------------------------------------

# Path placeholders use fixed test values so the handler reaches its body
# even if it tries a DB lookup (which 404s cleanly via maybe_single()).
TEST_JOB_ID = "test-job-1"
TEST_CAMPAIGN_ID = "00000000-0000-0000-0000-000000000001"


# Every authed endpoint. Liveness probe `/` is excluded — it has no auth.
AUTHED_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/leads"),
    ("POST", "/upload"),  # multipart, special handling
    ("POST", "/process-lead"),
    ("POST", "/process-all"),
    ("GET", "/audit-status"),
    ("POST", "/audit/stop"),
    ("GET", "/health/schema"),
    ("POST", "/ask"),
    ("GET", "/insights"),
    ("GET", "/stats"),
    ("POST", "/draft-outreach"),
    ("POST", "/draft-linkedin"),
    ("POST", "/execute"),
    ("POST", "/hunt-lead"),
    ("POST", "/hunt-all"),
    ("POST", "/discovery/start"),
    ("POST", "/enrich/start"),
    ("DELETE", "/leads/clear"),
    ("DELETE", "/leads/demo"),
    ("POST", "/orchestrator/start"),
    ("GET", f"/orchestrator/status/{TEST_JOB_ID}"),
    ("GET", "/orchestrator/active"),
    ("POST", f"/orchestrator/stop/{TEST_JOB_ID}"),
    ("GET", "/export"),
    ("GET", "/export/download"),
    ("GET", "/export/outreach"),
    ("POST", "/campaigns"),
    ("GET", "/campaigns"),
    ("GET", f"/campaigns/{TEST_CAMPAIGN_ID}"),
    ("POST", f"/campaigns/{TEST_CAMPAIGN_ID}/generate"),
    ("POST", f"/campaigns/{TEST_CAMPAIGN_ID}/start"),
    ("POST", f"/campaigns/{TEST_CAMPAIGN_ID}/pause"),
    ("GET", f"/campaigns/{TEST_CAMPAIGN_ID}/export"),
]
assert len(AUTHED_ENDPOINTS) == 33, len(AUTHED_ENDPOINTS)


# POST endpoints with a Pydantic JSON body. (Excludes /upload which is
# multipart, and POSTs with no body like /process-all.) For each: a valid
# minimal body, an extra-field probe, an over-length probe, and the
# string fields that should be fuzzed.
POST_WITH_BODY: dict[str, dict[str, Any]] = {
    "/process-lead": {
        "valid": {"unique_key": "valid-key"},
        "extra": {"unique_key": "valid-key", "extra_field": "x"},
        "over": {"unique_key": "x" * 129},  # constr max_length=128
        "string_fields": ["unique_key"],
    },
    "/ask": {
        "valid": {"instruction": {"text": "hello"}},
        "extra": {"instruction": {"text": "hello"}, "tracking_id": "x"},
        "over": {"instruction": {"text": "x" * 4001}},  # constr max_length=4000
        "string_fields": ["instruction.text"],
    },
    "/draft-outreach": {
        "valid": {"unique_key": "valid-key"},
        "extra": {"unique_key": "valid-key", "tone": "friendly"},
        "over": {"unique_key": "x" * 129},
        "string_fields": ["unique_key"],
    },
    "/draft-linkedin": {
        "valid": {"unique_key": "valid-key"},
        "extra": {"unique_key": "valid-key", "tone": "friendly"},
        "over": {"unique_key": "x" * 129},
        "string_fields": ["unique_key"],
    },
    "/execute": {
        "valid": {"task": "STATUS_CHECK", "params": {}},
        "extra": {"task": "STATUS_CHECK", "params": {}, "executor": "x"},
        "over": {
            "task": "STATUS_CHECK",
            "params": {"query": "x" * 501},
        },  # constr max_length=500
        "string_fields": ["task"],
    },
    "/hunt-lead": {
        "valid": {"unique_key": "valid-key"},
        "extra": {"unique_key": "valid-key", "deep": True},
        "over": {"unique_key": "x" * 129},
        "string_fields": ["unique_key"],
    },
    "/discovery/start": {
        "valid": {"query": "dentists", "location": "Mostar"},
        "extra": {"query": "dentists", "location": "Mostar", "engine": "google"},
        "over": {"query": "x" * 501, "location": "Mostar"},  # constr max_length=500
        "string_fields": ["query", "location"],
    },
    "/enrich/start": {
        "valid": {"unique_key": "valid-key"},
        "extra": {"unique_key": "valid-key", "deep": True},
        "over": {"unique_key": "x" * 129},
        "string_fields": ["unique_key"],
    },
    "/orchestrator/start": {
        "valid": {"filters": {}, "lead_ids": ["k1"], "tasks": ["audit"]},
        "extra": {"filters": {}, "tracking_id": "x"},
        "over": {"filters": {}, "tasks": ["x" * 65]},  # constr max_length=64
        "string_fields": ["filters"],  # filters is a dict — no string fuzz
    },
    "/campaigns": {  # POST only
        "valid": {"name": "C", "channel": "email"},
        "extra": {"name": "C", "channel": "email", "deleted": False},
        "over": {"name": "x" * 201, "channel": "email"},  # constr max_length=200
        "string_fields": ["name", "segment_filter"],
    },
}


# ---- Helpers ----------------------------------------------------------------


def _set_nested(d: dict, dotted: str, value: Any) -> dict:
    """`a.b` style nested field setter on a copy of d. Returns the copy."""
    out = {**d}
    if "." not in dotted:
        out[dotted] = value
        return out
    head, *rest = dotted.split(".")
    out[head] = _set_nested(dict(out.get(head) or {}), ".".join(rest), value)
    return out


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: Optional[dict] = None,
    json: Any = None,
    files: Any = None,
    content: Any = None,
) -> httpx.Response:
    return await client.request(
        method, path, headers=headers or {}, json=json, files=files, content=content
    )


# ---- Auth tests ------------------------------------------------------------


class TestAuthOnAllEndpoints(unittest.IsolatedAsyncioTestCase):
    """Every authed endpoint must 403 without/with-wrong X-API-Key."""

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_missing_key_returns_403_on_every_endpoint(self):
        failures = []
        for method, path in AUTHED_ENDPOINTS:
            # For POSTs that require a body, send a placeholder so we can
            # confirm the auth dependency fires BEFORE the body parser.
            json_body = None
            if method == "POST" and path in POST_WITH_BODY:
                json_body = POST_WITH_BODY[path]["valid"]
            files = None
            if path == "/upload":
                files = {"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}

            res = await _request(self.http, method, path, json=json_body, files=files)
            if res.status_code != 403:
                failures.append(
                    f"{method} {path}: got {res.status_code} (want 403)  body={res.text[:120]!r}"
                )
        self.assertFalse(
            failures, "Missing-key auth gate broken:\n" + "\n".join(failures)
        )

    async def test_wrong_key_returns_403_on_every_endpoint(self):
        wrong = {"X-API-Key": "definitely-not-the-real-key"}
        failures = []
        for method, path in AUTHED_ENDPOINTS:
            json_body = (
                POST_WITH_BODY.get(path, {}).get("valid") if method == "POST" else None
            )
            files = (
                {"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}
                if path == "/upload"
                else None
            )
            res = await _request(
                self.http, method, path, headers=wrong, json=json_body, files=files
            )
            if res.status_code != 403:
                failures.append(f"{method} {path}: got {res.status_code} (want 403)")
        self.assertFalse(
            failures, "Wrong-key auth gate broken:\n" + "\n".join(failures)
        )

    async def test_liveness_probe_unauthenticated(self):
        """`/` is intentionally exempt — it's the cold-start health check."""
        res = await self.http.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})


# ---- Body validation tests -------------------------------------------------


class TestBodyValidation(unittest.IsolatedAsyncioTestCase):
    """Body-required endpoints: empty / extra / over-length / valid-key gating."""

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_empty_body_returns_422_with_valid_key(self):
        """422 ONLY after auth passes (custom validation handler in
        _validation_with_authz_check, backend/main.py:351).

        Endpoints with all-optional body fields (e.g. /orchestrator/start
        accepts {} legitimately — filters / lead_ids / tasks all Optional)
        are excluded. The contract there is "200 / non-422 on empty body",
        which the auth+precedence test below already covers."""
        all_optional = {"/orchestrator/start"}
        failures = []
        for path in POST_WITH_BODY:
            if path in all_optional:
                continue
            res = await self.http.post(path, headers=self.h_ok, json={})
            if res.status_code != 422:
                failures.append(f"{path}: empty body got {res.status_code} (want 422)")
        self.assertFalse(failures, "Empty-body validation:\n" + "\n".join(failures))

    async def test_empty_body_with_missing_key_returns_403(self):
        """Auth precedence: even with invalid body, missing key wins (403)."""
        failures = []
        for path in POST_WITH_BODY:
            res = await self.http.post(path, json={})
            if res.status_code != 403:
                failures.append(
                    f"{path}: no-key + empty body got {res.status_code} (want 403)"
                )
        self.assertFalse(
            failures, "Auth-before-validation gate broken:\n" + "\n".join(failures)
        )

    async def test_extra_fields_rejected_via_extra_forbid(self):
        """Pydantic ConfigDict(extra='forbid') — surplus fields => 422."""
        failures = []
        for path, spec in POST_WITH_BODY.items():
            res = await self.http.post(path, headers=self.h_ok, json=spec["extra"])
            if res.status_code != 422:
                failures.append(
                    f"{path}: extra field got {res.status_code} (want 422)  body={res.text[:120]!r}"
                )
        self.assertFalse(failures, "extra='forbid' broken:\n" + "\n".join(failures))

    async def test_max_length_boundary_returns_422(self):
        """One char OVER constr max_length must be rejected by Pydantic."""
        failures = []
        for path, spec in POST_WITH_BODY.items():
            res = await self.http.post(path, headers=self.h_ok, json=spec["over"])
            if res.status_code != 422:
                failures.append(
                    f"{path}: over-length got {res.status_code} (want 422)  "
                    f"body={res.text[:140]!r}"
                )
        self.assertFalse(
            failures, "constr max_length not enforced:\n" + "\n".join(failures)
        )


# ---- Adversarial-string fuzz -----------------------------------------------

# Build adversarial codepoint inputs via chr() so the source file contains
# only ASCII — keeps semgrep's bidi-char detector happy while the runtime
# payload sent to the server is identical to a literal codepoint.
_ZWSP = chr(0x200B)  # zero-width space
_RLO = chr(0x202E)  # right-to-left override
_LRI = chr(0x2066)  # left-to-right isolate
_PDI = chr(0x2069)  # pop directional isolate

ADVERSARIAL_VALUES = [
    ("nul_byte", "valid\x00sneaky"),
    ("zero_width_space", f"valid{_ZWSP}sneaky"),
    ("rtl_override", f"valid{_RLO}sneaky"),
    ("emoji", "valid\U0001f6a8leaq"),
    ("embedded_json", 'valid"} or "1"="1'),
    ("escape_seq", "valid\\n\\r\\t"),
    ("unicode_bidi", f"test{_LRI}injection{_PDI}"),
]


class TestAdversarialStringFuzz(unittest.IsolatedAsyncioTestCase):
    """
    Each string field of each body-bearing POST is sent with adversarial
    values. The server may legitimately 200, 4xx, or 503 — but a 500 means
    we hit an unhandled exception. That's a real bug worth flagging.
    """

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_no_500_on_adversarial_strings(self):
        crashes = []
        for path, spec in POST_WITH_BODY.items():
            for field in spec["string_fields"]:
                # Skip non-string targets (e.g. orchestrator/start filters is a dict)
                placeholder = _set_nested(spec["valid"], field, "probe")
                # Confirm field is leaf-string-typed in the valid example
                # before fuzzing (skip if not).
                head = field.split(".")[0]
                if head not in placeholder:
                    continue
                for tag, adv in ADVERSARIAL_VALUES:
                    body = _set_nested(spec["valid"], field, adv)
                    res = await self.http.post(path, headers=self.h_ok, json=body)
                    if res.status_code >= 500 and res.status_code != 503:
                        # 503 is legit (Supabase not connected in mocks).
                        crashes.append(
                            f"{path}[{field}={tag}]: {res.status_code}  "
                            f"body={res.text[:160]!r}"
                        )
        self.assertFalse(
            crashes,
            "Server returned 500 on adversarial input (unhandled exception):\n"
            + "\n".join(crashes),
        )

    async def test_oversize_payload_rejected_via_pydantic(self):
        """A huge JSON blob in the unique_key field should not OOM the server."""
        huge = {"unique_key": "A" * 10_000}
        res = await self.http.post("/process-lead", headers=self.h_ok, json=huge)
        self.assertEqual(
            res.status_code,
            422,
            f"expected 422, got {res.status_code} body={res.text[:200]!r}",
        )


# ---- Rate-limit boundary ---------------------------------------------------


class TestRateLimitBoundary(unittest.IsolatedAsyncioTestCase):
    """
    /ask is decorated with @limiter.limit("10/minute"). 11 valid requests from
    the same source within the window must produce at least one 429.

    Caveats:
      - Fresh app per test class so slowapi memory storage starts empty.
      - We use a body that passes Pydantic validation so the limiter actually
        runs (it's after the auth dep but before the handler body).
    """

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_eleventh_ask_call_returns_429(self):
        body = {"instruction": {"text": "rate limit probe"}}
        # Fire 11 sequentially so they share the same time window. Parallel
        # gather is also fine for slowapi's atomic counter.
        statuses = []
        for _ in range(11):
            res = await self.http.post("/ask", headers=self.h_ok, json=body)
            statuses.append(res.status_code)
        self.assertIn(
            429,
            statuses,
            f"No 429 in 11 sequential /ask calls. Statuses: {statuses}. "
            f"slowapi limiter may be disabled or keyed too loosely.",
        )

    async def test_destructive_endpoint_3_per_hour_trips_at_4(self):
        """`DELETE /leads/clear` has @limiter.limit('3/hour'). #4 must 429."""
        h = {**self.h_ok, "X-Admin-Token": ADMIN_TOKEN}
        statuses = []
        for _ in range(4):
            res = await self.http.delete("/leads/clear", headers=h)
            statuses.append(res.status_code)
        self.assertIn(
            429,
            statuses,
            f"DELETE /leads/clear: 4 calls did not trip rate limit. Statuses: {statuses}",
        )


# ---- Admin token guard (DELETE /leads/clear) -------------------------------


class TestAdminTokenGuard(unittest.IsolatedAsyncioTestCase):
    """DELETE /leads/clear requires BOTH X-API-Key AND X-Admin-Token."""

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_no_admin_token_returns_403(self):
        res = await self.http.delete("/leads/clear", headers={"X-API-Key": API_KEY})
        self.assertEqual(
            res.status_code, 403, f"got {res.status_code} body={res.text!r}"
        )

    async def test_wrong_admin_token_returns_403(self):
        res = await self.http.delete(
            "/leads/clear",
            headers={
                "X-API-Key": API_KEY,
                "X-Admin-Token": "wrong-admin-token",
            },
        )
        self.assertEqual(
            res.status_code, 403, f"got {res.status_code} body={res.text!r}"
        )

    async def test_no_api_key_takes_precedence_over_admin_token(self):
        """Auth ordering: API key dep is declared first, so it must fire even
        with a valid admin token."""
        res = await self.http.delete(
            "/leads/clear",
            headers={
                "X-Admin-Token": ADMIN_TOKEN,
            },
        )
        self.assertEqual(
            res.status_code, 403, f"got {res.status_code} body={res.text!r}"
        )

    async def test_leads_demo_requires_admin_token(self):
        """DELETE /leads/demo shares the same triple gate as /leads/clear
        (API key + admin token + typed Pydantic body). Probing without the
        admin token must short-circuit at 403 before Pydantic even sees
        the JSON body.

        httpx note: `AsyncClient.delete()` does NOT accept a `json=` kwarg
        (only `headers` / `params`). DELETE-with-body must go through
        `request("DELETE", ..., json=...)` — the asymmetry is documented
        upstream as a guard against accidental body-on-DELETE in idempotent
        contexts. Our handler explicitly accepts a typed Pydantic body,
        so we route via `request(...)`.
        """
        res = await self.http.request(
            "DELETE",
            "/leads/demo",
            headers={"X-API-Key": API_KEY},
            json={"confirmation": "REMOVE DEMO"},
        )
        self.assertEqual(
            res.status_code, 403, f"got {res.status_code} body={res.text!r}"
        )

    async def test_leads_demo_wrong_confirmation_returns_422(self):
        """With both keys present, a body that doesn't carry the exact
        Literal["REMOVE DEMO"] must 422 via Pydantic — the handler never
        runs and no rows are deleted. See sibling test's docstring for
        the `httpx.request("DELETE", ...)` rationale."""
        res = await self.http.request(
            "DELETE",
            "/leads/demo",
            headers={"X-API-Key": API_KEY, "X-Admin-Token": ADMIN_TOKEN},
            json={"confirmation": "remove demo"},
        )
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text!r}"
        )


# ---- /execute task allowlist (defense-in-depth) ----------------------------


class TestExecuteTaskAllowlist(unittest.IsolatedAsyncioTestCase):
    """Locked in by tests/test_execute_plan_model.py too — duplicate here as
    an integration test (POST flow + Pydantic + handler dispatch)."""

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_unknown_task_rejected(self):
        res = await self.http.post(
            "/execute", headers=self.h_ok, json={"task": "DELETE_ALL_LEADS"}
        )
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_valid_task_accepted(self):
        # Body validation must pass for an allowlisted task.
        res = await self.http.post(
            "/execute", headers=self.h_ok, json={"task": "STATUS_CHECK", "params": {}}
        )
        # The handler will then run with a mock router (returns {"message":"ok"}).
        self.assertNotEqual(res.status_code, 422)
        self.assertNotEqual(res.status_code, 403)


# ---- PipelineRequest.filters typed allowlist (M5 hardening) ----------------


class TestPipelineFiltersTyped(unittest.IsolatedAsyncioTestCase):
    """
    `PipelineRequest.filters` used to be `Optional[dict]` — the only field
    in any inbound model without `extra='forbid'` + bounded `constr`.
    That escape hatch let an authed caller smuggle arbitrary keys + nested
    dict-shaped values past Pydantic.

    This class locks in the typed `PipelineFilters` allowlist
    (`type`/`query`/`location`/`limit`). Anything outside the allowlist
    must 422. Bounds (constr 200 / 64 / ge=1 / le=1000) must trip at
    boundary + 1.
    """

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def _post_pipeline(self, body):
        return await self.http.post("/orchestrator/start", headers=self.h_ok, json=body)

    def _assert_pydantic_accepted(self, res):
        """Pydantic validation passed if status is NOT 422 and NOT 403.

        Test fixture sets `mock_db.client = None`, so the handler then
        short-circuits to 503 ("Database not connected"). 503 here means
        validation passed and the handler ran — exactly what we're
        proving. Anything 4xx other than 422/403 would also indicate the
        wrong gate fired."""
        self.assertNotEqual(
            res.status_code,
            422,
            f"Pydantic rejected valid body: {res.status_code} {res.text[:200]!r}",
        )
        self.assertNotEqual(
            res.status_code,
            403,
            f"Auth rejected (test misconfigured): {res.status_code} {res.text[:200]!r}",
        )
        # 200 (mock pipeline returns) or 503 (db.client is None) both indicate
        # the validator passed; either is acceptable here.
        self.assertIn(
            res.status_code,
            (200, 503),
            f"Unexpected status: {res.status_code} {res.text[:200]!r}",
        )

    # ---- Accept paths --------------------------------------------------

    async def test_type_only_accepted(self):
        """Matches the AI-router shape (`{"type": ...}`)."""
        res = await self._post_pipeline({"filters": {"type": "ev_charger"}})
        self._assert_pydantic_accepted(res)

    async def test_query_plus_location_accepted(self):
        """Matches the discovery shape (`{"query": ..., "location": ...}`)."""
        res = await self._post_pipeline(
            {"filters": {"query": "dentist", "location": "Mostar"}}
        )
        self._assert_pydantic_accepted(res)

    async def test_filters_null_accepted(self):
        """`filters=null` is the default — pipeline runs against the full
        lead inventory."""
        res = await self._post_pipeline({"filters": None})
        self._assert_pydantic_accepted(res)

    async def test_filters_empty_dict_accepted(self):
        """`{}` is valid — every PipelineFilters key is Optional."""
        res = await self._post_pipeline({"filters": {}})
        self._assert_pydantic_accepted(res)

    async def test_limit_at_lower_bound_accepted(self):
        res = await self._post_pipeline({"filters": {"limit": 1}})
        self._assert_pydantic_accepted(res)

    async def test_limit_at_upper_bound_accepted(self):
        res = await self._post_pipeline({"filters": {"limit": 1000}})
        self._assert_pydantic_accepted(res)

    # ---- Reject paths --------------------------------------------------

    async def test_extra_filter_key_rejected_422(self):
        """`PipelineFilters` is `extra='forbid'` — unknown keys must 422.
        This is the M5 fix: the old `Optional[dict]` accepted any key."""
        res = await self._post_pipeline({"filters": {"type": "x", "extra_key": "y"}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_arbitrary_db_column_key_rejected_422(self):
        """A caller probing for orchestrator-side allowlist columns
        (`segment`, `audit_status`, etc.) must 422 at the HTTP edge."""
        res = await self._post_pipeline({"filters": {"segment": "dental"}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_limit_zero_rejected_422(self):
        """`ge=1` — zero or negative must 422."""
        res = await self._post_pipeline({"filters": {"limit": 0}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_limit_above_max_rejected_422(self):
        """`le=1000` — 1001 must 422."""
        res = await self._post_pipeline({"filters": {"limit": 1001}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_query_over_max_length_rejected_422(self):
        """`constr(max_length=200)` — 201 chars must 422."""
        res = await self._post_pipeline({"filters": {"query": "x" * 201}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_location_over_max_length_rejected_422(self):
        """`constr(max_length=200)` — 201 chars must 422."""
        res = await self._post_pipeline({"filters": {"location": "y" * 201}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_type_over_max_length_rejected_422(self):
        """`constr(max_length=64)` — 65 chars must 422."""
        res = await self._post_pipeline({"filters": {"type": "t" * 65}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )

    async def test_nested_dict_value_rejected_422(self):
        """A nested dict at any leaf key must 422 — closes the deep-nest
        smuggle surface the old `Optional[dict]` allowed."""
        res = await self._post_pipeline({"filters": {"type": {"smuggled": "object"}}})
        self.assertEqual(
            res.status_code, 422, f"got {res.status_code} body={res.text[:200]!r}"
        )


if __name__ == "__main__":
    unittest.main()
