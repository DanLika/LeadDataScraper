"""Endpoint tests for ``GET /admin/gemini-budget``.

Verifies the two-factor gate (X-API-Key + X-Admin-Token), the shape
of the response body, and the slowapi rate limit.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.append(str(_BACKEND))


API_KEY = "test-budget-endpoint-key"
ADMIN_TOKEN = "test-budget-admin-token"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("GEMINI_BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1000000")


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
    """Same priming pattern as test_json_pollution.py — the TestClient
    bypasses the lifespan that normally caches db/router/auditor/
    orchestrator into globals, so handlers that reference them bare
    would NameError-500."""
    import main as backend_main
    from unittest.mock import AsyncMock, MagicMock

    captured = {
        name: backend_main.__dict__.pop(name, None)
        for name in ("db", "router", "auditor", "orchestrator")
    }
    backend_main.db = MagicMock(client=MagicMock())
    backend_main.router = MagicMock(execute_task=AsyncMock(return_value={}))
    backend_main.auditor = MagicMock()
    backend_main.orchestrator = MagicMock()
    yield
    for name, prev in captured.items():
        if prev is not None:
            setattr(backend_main, name, prev)
        else:
            backend_main.__dict__.pop(name, None)


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


class TestAdminGeminiBudgetEndpoint:
    def test_missing_api_key_403(self, client):
        # No X-API-Key at all.
        resp = client.get("/admin/gemini-budget")
        assert resp.status_code == 403
        # Generic error body — must NOT leak the admin gate's presence.
        body = resp.json()
        assert body.get("detail") == "Invalid or missing API key"

    def test_wrong_api_key_403(self, client):
        resp = client.get(
            "/admin/gemini-budget",
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 403

    def test_api_key_only_no_admin_token_403(self, client):
        # Authenticated but missing the admin gate.
        resp = client.get(
            "/admin/gemini-budget",
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 403
        body = resp.json()
        # The admin gate's error message — locks in the layered gate
        # surface so a future refactor doesn't quietly collapse it.
        assert body.get("detail") == "Invalid or missing admin token"

    def test_wrong_admin_token_403(self, client):
        resp = client.get(
            "/admin/gemini-budget",
            headers={"X-API-Key": API_KEY, "X-Admin-Token": "wrong"},
        )
        assert resp.status_code == 403

    def test_both_factors_present_200_with_shape(self, client):
        resp = client.get(
            "/admin/gemini-budget",
            headers={"X-API-Key": API_KEY, "X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Schema lock: every key documented in the budget module must
        # appear in the JSON response.
        assert set(body.keys()) == {
            "date",
            "used_today",
            "input_today",
            "output_today",
            "ceiling",
            "remaining",
            "reset_at_utc",
        }
        # Fresh DB → counters at zero, ceiling honoured.
        assert body["used_today"] == 0
        assert body["input_today"] == 0
        assert body["output_today"] == 0
        assert body["ceiling"] == 1000000
        assert body["remaining"] == 1000000
        assert body["reset_at_utc"].endswith("Z")

    def test_admin_token_missing_in_env_returns_403(self, client, monkeypatch):
        """If the operator forgot to set ADMIN_TOKEN, the admin gate
        must refuse even when the API key is correct.  Same posture
        as `/leads/clear`."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        resp = client.get(
            "/admin/gemini-budget",
            headers={"X-API-Key": API_KEY, "X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 403

    def test_rate_limit_429_after_60(self, client):
        # The endpoint is rate-limited at 60/minute; burst 61 to trip it.
        # Reset the slowapi store first (done by autouse fixture); fire
        # the burst in a loop.
        headers = {"X-API-Key": API_KEY, "X-Admin-Token": ADMIN_TOKEN}
        successes = 0
        rate_limited = 0
        for _ in range(70):
            r = client.get("/admin/gemini-budget", headers=headers)
            if r.status_code == 200:
                successes += 1
            elif r.status_code == 429:
                rate_limited += 1
            else:
                pytest.fail(f"unexpected status {r.status_code}: {r.text}")
        # We expect roughly 60 successes and the remainder 429.  Tolerate
        # a small jitter window in either direction because slowapi's
        # moving-window storage doesn't guarantee an exact integer cutoff.
        assert successes <= 60
        assert rate_limited >= 10


class TestBudgetExceededExceptionHandler:
    """Verify the FastAPI dispatch picks up the dedicated
    ``BudgetExceededError`` handler — without this test the unit test
    that asserts ``raises(BudgetExceededError)`` from the helper is
    not enough.  FastAPI's dispatch order normally prefers the most
    specific exception class, but a future registration-order bug
    could quietly route it to the generic ``Exception`` 500."""

    def test_budget_error_in_draft_outreach_returns_503(self, client, monkeypatch):
        """``/draft-outreach`` has NO try/except around its router call,
        so a BudgetExceededError propagates naturally to the
        ``@app.exception_handler(BudgetExceededError)`` we registered."""
        from src.utils.gemini_budget import BudgetExceededError
        import main as backend_main

        async def raises_budget(*args, **kwargs):
            raise BudgetExceededError(used_today=999_999, ceiling=1_000_000)

        backend_main.router.execute_task = raises_budget  # type: ignore[assignment]
        resp = client.post(
            "/draft-outreach",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"unique_key": "lead-1234"},
        )
        assert resp.status_code == 503, (
            f"expected 503 budget-exceeded, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body == {"error": "AI daily budget exhausted"}

    def test_budget_error_in_ask_returns_503(self, client, monkeypatch):
        """``/ask`` wraps the router in ``try/except Exception`` — the
        generic catch would swallow ``BudgetExceededError`` to a 500
        UNLESS we add ``except BudgetExceededError: raise`` before it
        (which we did).  This test locks the re-raise in place — if
        a future refactor removes it, the handler will start returning
        a 500 instead of the canonical 503."""
        from src.utils.gemini_budget import BudgetExceededError
        import main as backend_main

        async def raises_budget(*args, **kwargs):
            raise BudgetExceededError(used_today=999_999, ceiling=1_000_000)

        # /ask uses `router.route_instruction` first; if that succeeds
        # and the task is DATABASE_QUERY/STATUS_CHECK/GET_INSIGHTS it
        # then calls `router.execute_task`.  Raise from
        # route_instruction so the test doesn't depend on the plan path.
        backend_main.router.route_instruction = raises_budget  # type: ignore[assignment]
        resp = client.post(
            "/ask",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json={"instruction": {"text": "How many leads?"}},
        )
        assert resp.status_code == 503, (
            f"expected 503 budget-exceeded, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body == {"error": "AI daily budget exhausted"}

    def test_budget_error_in_insights_returns_503(self, client, monkeypatch):
        """Same pattern as /ask — `/insights` also wraps in try/except."""
        from src.utils.gemini_budget import BudgetExceededError
        import main as backend_main

        async def raises_budget(*args, **kwargs):
            raise BudgetExceededError(used_today=999_999, ceiling=1_000_000)

        backend_main.router.execute_task = raises_budget  # type: ignore[assignment]
        resp = client.get(
            "/insights",
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body == {"error": "AI daily budget exhausted"}
