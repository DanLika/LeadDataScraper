"""Pin ``safe_constr`` + every endpoint that adopts it.

Three layers:

1. **Unit** — the underlying ``_reject_control_or_format`` helper accepts
   tab/newline/carriage-return and rejects every other Cc/Cf codepoint
   including the historical offenders (NUL, ZWS, RTL override).
2. **Model** — a synthetic Pydantic model using ``safe_constr`` round-trips
   the accept/reject contract.
3. **HTTP** — every backend endpoint that holds a user-facing string
   field is exercised against the real ASGI app with adversarial input.
   Expected: ``422`` with detail "control or format character not
   allowed". Pre-fix behaviour returned 500 on at least two of these
   (``POST /discovery/start`` and ``POST /campaigns``); the test exists
   to prevent regression.

The HTTP layer mirrors ``tests/test_endpoint_hardening.py`` plumbing:
mocks heavy module-level singletons before any request fires so the
validation layer runs against the real Pydantic models without
touching live Supabase/Gemini/Playwright.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import unittest
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport
from pydantic import BaseModel, ConfigDict, ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.schemas.sanitized_str import _reject_control_or_format, safe_constr

API_KEY = "test-api-key-correct"
ADMIN_TOKEN = "test-admin-token-correct"

# Adversarial samples — every one MUST be rejected by the validator.
# Every non-ASCII codepoint is built via chr() so the source file on
# disk stays pure ASCII (no bidi/zero-width at rest; scanner-friendly).
ADVERSARIAL_SAMPLES = [
    ("nul",                 "before" + chr(0x00)   + "after"),
    ("zero-width-space",    "before" + chr(0x200b) + "after"),
    ("zero-width-joiner",   "before" + chr(0x200d) + "after"),
    ("rtl-override",        "before" + chr(0x202e) + "after"),
    ("ltr-override",        "before" + chr(0x202d) + "after"),
    ("bom",                 "before" + chr(0xfeff) + "after"),
    ("vertical-tab",        "before" + chr(0x0b)   + "after"),
    ("form-feed",           "before" + chr(0x0c)   + "after"),
    ("backspace",           "before" + chr(0x08)   + "after"),
    ("ascii-bell",          "before" + chr(0x07)   + "after"),
    ("pile-of-poo-not-control", None),  # sentinel; emoji must be ALLOWED
]

ALLOWED_SAMPLES = [
    ("ascii-only",                       "Hello, World!"),
    ("with-tab",                         "name\tvalue"),
    ("with-newline",                     "line1\nline2"),
    ("with-cr",                          "line1\rline2"),
    ("with-crlf",                        "line1\r\nline2"),
    # All non-ASCII letters/emoji/symbols built via chr() to keep this
    # file's bytes ASCII-only.
    ("non-ascii-letters",                "caf" + chr(0x00e9)),         # é
    ("emoji",                            "All good " + chr(0x1f4a9)),   # pile of poo
    ("cjk",                              chr(0x4e2d) + chr(0x6587)),    # zhong wen
    ("arabic-rtl-letters-not-override",  chr(0x0627) + chr(0x0644)),    # alef + lam
    ("math-symbols",                     "x " + chr(0x2264) + " y"),    # ≤
]


# ───────────────────────────── Unit ─────────────────────────────────


class TestRejectControlOrFormatUnit(unittest.TestCase):
    """The underlying helper — straight Python, no Pydantic."""

    def test_allowed_inputs_pass_through_unchanged(self):
        for tag, value in ALLOWED_SAMPLES:
            with self.subTest(tag=tag):
                self.assertEqual(_reject_control_or_format(value), value)

    def test_each_adversarial_sample_raises(self):
        for tag, value in ADVERSARIAL_SAMPLES:
            if value is None:
                continue
            with self.subTest(tag=tag):
                with self.assertRaises(ValueError) as ctx:
                    _reject_control_or_format(value)
                msg = str(ctx.exception)
                self.assertIn("control or format character", msg)
                self.assertIn("index ", msg)
                self.assertIn("U+", msg)

    def test_empty_string_passes(self):
        self.assertEqual(_reject_control_or_format(""), "")

    def test_only_allowed_whitespace_passes(self):
        self.assertEqual(_reject_control_or_format("\t\n\r"), "\t\n\r")

    def test_error_reports_first_bad_char_index(self):
        with self.assertRaises(ValueError) as ctx:
            _reject_control_or_format("ok\x00bad")
        self.assertIn("index 2", str(ctx.exception))


# ────────────────────────── Model integration ──────────────────────


class _Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: safe_constr(min_length=1, max_length=64)


class TestSafeConstrModel(unittest.TestCase):
    """safe_constr embedded in a real Pydantic model."""

    def test_valid_inputs_construct(self):
        for tag, value in ALLOWED_SAMPLES:
            with self.subTest(tag=tag):
                _Sample(label=value)  # no raise

    def test_length_constraint_still_fires(self):
        with self.assertRaises(ValidationError):
            _Sample(label="x" * 65)
        with self.assertRaises(ValidationError):
            _Sample(label="")  # below min

    def test_control_char_raises_validation_error(self):
        for tag, value in ADVERSARIAL_SAMPLES:
            if value is None:
                continue
            with self.subTest(tag=tag):
                with self.assertRaises(ValidationError) as ctx:
                    _Sample(label=value)
                # The Pydantic error array should mention our message
                errors = ctx.exception.errors()
                self.assertTrue(any(
                    "control or format character" in (e.get("msg") or "")
                    for e in errors
                ), f"errors did not surface our message: {errors!r}")

    def test_length_and_control_compose_length_first(self):
        # Over-length string with NUL — Pydantic checks length BEFORE
        # AfterValidator runs. We get the length error, not our error.
        with self.assertRaises(ValidationError) as ctx:
            _Sample(label="x" * 65 + "\x00")
        errors = ctx.exception.errors()
        # at least one error mentions string_too_long (the length one)
        self.assertTrue(any(e.get("type") == "string_too_long" for e in errors))


# ────────────────────────── HTTP integration ───────────────────────


def _fresh_app():
    """Re-import backend.main with secrets + mocked heavy singletons.

    Mirrors tests/test_endpoint_hardening.py — the validation layer
    we exercise runs BEFORE the handler body, so mocking the DB /
    AI / orchestrator is safe and just keeps side effects out.
    """
    os.environ["API_SECRET_KEY"] = API_KEY
    os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
    os.environ["DISABLE_LIFESPAN_RECOVERY"] = "1"

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    if "backend.main" in sys.modules:
        del sys.modules["backend.main"]
    backend_main = importlib.import_module("backend.main")
    backend_main.app.router.lifespan_context = _noop_lifespan

    # Stub the heavy singletons.
    for attr in ("db", "router", "auditor", "orchestrator"):
        try:
            obj = getattr(backend_main, attr)
            mock = MagicMock()
            for method_name in dir(obj):
                if not method_name.startswith("_"):
                    setattr(mock, method_name, AsyncMock())
            mock.client = MagicMock()
            setattr(backend_main, attr, mock)
        except Exception:
            pass
    return backend_main.app


def _client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# (path, body builder taking the adversarial value, path-field name for subTest)
ENDPOINTS_TO_FUZZ = [
    ("/process-lead",       lambda v: {"unique_key": v},                       "unique_key"),
    ("/draft-outreach",     lambda v: {"unique_key": v},                       "unique_key"),
    ("/draft-linkedin",     lambda v: {"unique_key": v},                       "unique_key"),
    ("/hunt-lead",          lambda v: {"unique_key": v},                       "unique_key"),
    ("/enrich/start",       lambda v: {"unique_key": v},                       "unique_key"),
    ("/ask",                lambda v: {"instruction": {"text": v}},            "instruction.text"),
    ("/execute",            lambda v: {"task": "STATUS_CHECK",
                                       "params": {"query": v}},                "params.query"),
    ("/execute",            lambda v: {"task": "STATUS_CHECK",
                                       "params": {"query_text": v}},           "params.query_text"),
    ("/execute",            lambda v: {"task": "STATUS_CHECK",
                                       "params": {"location": v}},             "params.location"),
    ("/execute",            lambda v: {"task": "STATUS_CHECK",
                                       "params": {"filters": v}},              "params.filters"),
    ("/execute",            lambda v: {"task": "STATUS_CHECK",
                                       "params": {"unique_key": v}},           "params.unique_key"),
    ("/discovery/start",    lambda v: {"query": v},                            "query"),
    ("/discovery/start",    lambda v: {"query": "ok", "location": v},          "location"),
    ("/campaigns",          lambda v: {"name": v, "channel": "email"},         "name"),
    ("/campaigns",          lambda v: {"name": "ok", "channel": "email",
                                       "segment_filter": v},                   "segment_filter"),
    ("/orchestrator/start", lambda v: {"filters": {"query": v}},               "filters.query"),
    ("/orchestrator/start", lambda v: {"filters": {"location": v}},            "filters.location"),
    ("/orchestrator/start", lambda v: {"filters": {"type": v}},                "filters.type"),
    ("/orchestrator/start", lambda v: {"lead_ids": [v]},                       "lead_ids[0]"),
    ("/orchestrator/start", lambda v: {"tasks": [v]},                          "tasks[0]"),
    ("/audit-batch",        lambda v: {"lead_ids": [v]},                       "lead_ids[0]"),
    ("/metrics",            lambda v: {"name": "LCP", "value": 1.0,
                                       "rating": "good", "path": v,
                                       "id": "qatest"},                        "path"),
    ("/metrics",            lambda v: {"name": "LCP", "value": 1.0,
                                       "rating": "good", "path": "/x",
                                       "id": v},                               "id"),
]


class TestHTTPControlCharRejection(unittest.IsolatedAsyncioTestCase):
    """Real FastAPI app — every adopted endpoint rejects adversarial input."""

    async def asyncSetUp(self):
        self.app = _fresh_app()
        self.http = _client(self.app)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_every_endpoint_rejects_each_adversarial_sample(self):
        misses: list[tuple[str, str, str, int, str]] = []
        h_ok = {"X-API-Key": API_KEY}
        for path, builder, field in ENDPOINTS_TO_FUZZ:
            for tag, adv in ADVERSARIAL_SAMPLES:
                if adv is None:
                    continue
                body = builder(adv)
                res = await self.http.post(path, headers=h_ok, json=body)
                if res.status_code != 422:
                    misses.append((path, field, tag, res.status_code,
                                    res.text[:120]))
                    continue
                # Detail should mention our specific error message.
                detail = res.json().get("detail") or []
                ok = any(
                    "control or format character" in (e.get("msg") or "")
                    for e in (detail if isinstance(detail, list) else [])
                )
                if not ok:
                    misses.append((path, field, tag, res.status_code,
                                    str(detail)[:200]))
        self.assertEqual(misses, [],
            "endpoints did not reject adversarial input with 422+our message:\n"
            + "\n".join(f"  {m}" for m in misses))

# Note: the accept side of the contract is pinned by TestSafeConstrModel
# above (real Pydantic, no HTTP) and TestRejectControlOrFormatUnit
# (pure Python). An HTTP-layer accept test is brittle in a
# MagicMock-stubbed handler context (the handler returns a MagicMock,
# FastAPI's encoder crashes on `dict(<MagicMock>)`, which masks the
# validator's behaviour). The unit + model tests carry the load.


if __name__ == "__main__":
    unittest.main()
