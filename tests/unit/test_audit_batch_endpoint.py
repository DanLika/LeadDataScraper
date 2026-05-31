"""Lock the `/audit-batch` Pydantic contract + orchestrator call shape.

The wider `test_endpoint_hardening.py` covers auth, extra='forbid',
per-id max_length, and the adversarial-string fuzz battery. This file
adds the bits that registry can't express:

  1. Empty `lead_ids` list -> 422 (conlist min_length=1).
  2. `lead_ids` over the 200-cap -> 422 (conlist max_length=200).
  3. Happy path forwards the full caller list into
     `orchestrator.run_massive_pipeline(lead_ids=...)` with the
     `tasks=['audit']` flag — important: the bulk button must NOT
     trigger enrichment, because the operator already enriched these
     leads earlier in the funnel.

No live Gemini / Supabase — the orchestrator is mocked, mirroring
`test_endpoint_hardening.py`'s app builder.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
from httpx import ASGITransport

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

API_KEY = "test-api-key-batch"


def _fresh_app():
    os.environ["API_SECRET_KEY"] = API_KEY
    os.environ["DISABLE_LIFESPAN_RECOVERY"] = "1"

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    if "backend.main" in sys.modules:
        del sys.modules["backend.main"]
    backend_main = importlib.import_module("backend.main")
    backend_main.app.router.lifespan_context = _noop_lifespan

    mock_orchestrator = MagicMock(name="mock_orchestrator")
    mock_orchestrator.run_massive_pipeline = AsyncMock(return_value="job-batch-1")
    backend_main.orchestrator = mock_orchestrator

    return backend_main.app, mock_orchestrator


def _client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestAuditBatchEndpoint(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.app, self.orch = _fresh_app()
        self.http = _client(self.app)
        self.h_ok = {"X-API-Key": API_KEY}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_empty_list_rejected(self):
        res = await self.http.post(
            "/audit-batch", headers=self.h_ok, json={"lead_ids": []}
        )
        self.assertEqual(res.status_code, 422, res.text)
        self.orch.run_massive_pipeline.assert_not_called()

    async def test_over_two_hundred_rejected(self):
        res = await self.http.post(
            "/audit-batch",
            headers=self.h_ok,
            json={"lead_ids": [f"k{i}" for i in range(201)]},
        )
        self.assertEqual(res.status_code, 422, res.text)
        self.orch.run_massive_pipeline.assert_not_called()

    async def test_happy_path_forwards_list_with_audit_only(self):
        ids = ["alpha", "bravo", "charlie"]
        res = await self.http.post(
            "/audit-batch", headers=self.h_ok, json={"lead_ids": ids}
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["status"], "job_started")
        self.assertEqual(body["job_id"], "job-batch-1")
        self.assertEqual(body["count"], 3)
        self.orch.run_massive_pipeline.assert_awaited_once_with(
            lead_ids=ids, tasks=["audit"]
        )

    async def test_extra_field_rejected(self):
        res = await self.http.post(
            "/audit-batch",
            headers=self.h_ok,
            json={"lead_ids": ["k1"], "tasks": ["enrich"]},
        )
        self.assertEqual(res.status_code, 422, res.text)
        self.orch.run_massive_pipeline.assert_not_called()

    async def test_missing_api_key_rejected(self):
        res = await self.http.post("/audit-batch", json={"lead_ids": ["k1"]})
        self.assertEqual(res.status_code, 403, res.text)
        self.orch.run_massive_pipeline.assert_not_called()
