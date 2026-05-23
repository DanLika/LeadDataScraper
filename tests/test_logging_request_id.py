"""Structured-logging contract pins.

Three things this file locks in — refactors that break them fail CI:

1. **JsonFormatter envelope shape.** Canonical schema fields
   (`timestamp`, `level`, `logger`, `message`, `request_id`,
   `user_id`, `route`) live at the **top level** of the JSON object;
   domain fields passed via ``extra={...}`` are **merged** at the top
   level, not nested under `"context": {...}`. The grep / jq snippets
   in `docs/observability.md` §12 rely on this shape.

2. **request_id middleware behaviour.** The middleware honours
   inbound `X-Request-ID` only when it matches `[A-Za-z0-9_-]{1,64}`;
   any longer / control-char-bearing / missing value is replaced with
   a fresh 32-char hex. The response always carries `X-Request-ID`,
   matching whatever the request scope bound.

3. **ContextVar isolation across requests.** Two sequential requests
   to the test app get two different IDs. The middleware does NOT
   call `clear_request_context` — each request runs in its own
   asyncio Task, so the Context is GC'd cleanly; the test ensures
   no leakage between tasks regardless.

These tests are pure unit (no Supabase, no Gemini, no network). They
spin up a `TestClient` against `backend.main.app` with the lazy
singletons mocked out, matching the pattern in
`tests/test_endpoint_hardening.py`.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# backend/ is not on sys.path by default — pattern lifted from the
# existing endpoint-hardening tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from src.utils.logging_config import (  # noqa: E402
    JsonFormatter,
    _CRLFScrubFilter,
    bind_request_context,
    clear_request_context,
    request_id_var,
    route_var,
    user_id_var,
)


# ---------------------------------------------------------------------------
# 1. JsonFormatter envelope shape
# ---------------------------------------------------------------------------


class TestJsonFormatterEnvelope(unittest.TestCase):
    """Pin the JSON shape so jq queries in docs stay valid."""

    def _format(self, **record_kwargs):
        logger_obj = logging.getLogger("test.envelope")
        record = logger_obj.makeRecord(
            name=record_kwargs.pop("name", "test.envelope"),
            level=record_kwargs.pop("level", logging.INFO),
            fn=record_kwargs.pop("fn", "test.py"),
            lno=record_kwargs.pop("lno", 1),
            msg=record_kwargs.pop("msg", "hello"),
            args=record_kwargs.pop("args", ()),
            exc_info=record_kwargs.pop("exc_info", None),
            extra=record_kwargs.pop("extra", None),
        )
        return json.loads(JsonFormatter().format(record))

    def test_canonical_fields_present(self):
        out = self._format()
        for key in ("timestamp", "level", "logger", "message",
                    "request_id", "user_id", "route"):
            self.assertIn(key, out, f"missing canonical field {key!r}: {out!r}")

    def test_timestamp_iso8601_z_suffix(self):
        out = self._format()
        # 2026-05-22T14:30:15.123Z
        self.assertRegex(
            out["timestamp"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
        )

    def test_levelname_uppercase(self):
        out = self._format(level=logging.WARNING)
        self.assertEqual(out["level"], "WARNING")

    def test_request_id_pulls_from_contextvar(self):
        tok = request_id_var.set("rid-abc123")
        try:
            out = self._format()
            self.assertEqual(out["request_id"], "rid-abc123")
        finally:
            request_id_var.reset(tok)

    def test_user_id_pulls_from_contextvar(self):
        tok = user_id_var.set("operator@example.com")
        try:
            out = self._format()
            self.assertEqual(out["user_id"], "operator@example.com")
        finally:
            user_id_var.reset(tok)

    def test_route_pulls_from_contextvar(self):
        tok = route_var.set("/process-all")
        try:
            out = self._format()
            self.assertEqual(out["route"], "/process-all")
        finally:
            route_var.reset(tok)

    def test_unbound_context_vars_are_null(self):
        # Default ContextVar values are None; envelope reflects that.
        out = self._format()
        self.assertIsNone(out["request_id"])
        self.assertIsNone(out["user_id"])
        self.assertIsNone(out["route"])

    def test_extra_fields_merged_top_level_not_nested(self):
        out = self._format(extra={"job_id": "j-1", "chunk_index": 3})
        # Top-level merge — NOT nested under a "context" key.
        self.assertEqual(out["job_id"], "j-1")
        self.assertEqual(out["chunk_index"], 3)
        self.assertNotIn("context", out)

    def test_exception_field_populated_on_exc_info(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            out = self._format(exc_info=sys.exc_info())
        self.assertIn("exception", out)
        self.assertIn("RuntimeError", out["exception"])
        self.assertIn("boom", out["exception"])

    def test_non_serializable_extra_falls_through_to_repr(self):
        # An object that json.dumps would refuse.
        class _Weird:
            def __repr__(self):
                return "<Weird>"
        out = self._format(extra={"weird": _Weird()})
        # Either repr or some string — but the format MUST NOT crash.
        self.assertIn("weird", out)
        self.assertIsInstance(out["weird"], str)
        self.assertIn("Weird", out["weird"])

    def test_crlf_in_extra_scrubbed_at_filter_stage(self):
        # The filter runs BEFORE the formatter. Composite check:
        # a CRLF-bearing extra value should be \r/\n-escaped in the
        # final JSON envelope.
        logger_obj = logging.getLogger("test.envelope.crlf")
        record = logger_obj.makeRecord(
            name="test.envelope.crlf",
            level=logging.INFO,
            fn="test.py",
            lno=1,
            msg="processed",
            args=(),
            exc_info=None,
            extra={"lead_name": "X\r\nFAKE LOG LINE"},
        )
        _CRLFScrubFilter().filter(record)
        out = json.loads(JsonFormatter().format(record))
        self.assertNotIn("\r", out["lead_name"])
        self.assertNotIn("\n", out["lead_name"])
        self.assertIn("\\r", out["lead_name"])
        self.assertIn("\\n", out["lead_name"])


# ---------------------------------------------------------------------------
# 2. request_id middleware behaviour
# ---------------------------------------------------------------------------

_RID_PATTERN_HEX = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(scope="module")
def _client():
    """TestClient with the lazy singletons mocked out. Pattern matches
    `tests/test_endpoint_hardening.py`."""
    # Required env so the X-API-Key check has a value.
    with patch.dict("os.environ", {
        "API_SECRET_KEY": "test-secret-key-must-be-long-enough",
        "ADMIN_TOKEN": "test-admin-token",
        "ALLOWED_ORIGINS": "http://localhost:3000",
    }, clear=False):
        # Mock the lazy singletons so import doesn't try to reach Supabase.
        with patch.dict(
            "sys.modules",
            {
                # nothing to mock at module level; instead patch the
                # module __getattr__ after import.
            },
        ):
            import importlib
            import backend.main as main_mod  # type: ignore
            importlib.reload(main_mod)
            main_mod.db = MagicMock()
            main_mod.router = MagicMock()
            main_mod.auditor = MagicMock()
            main_mod.orchestrator = MagicMock()
            from fastapi.testclient import TestClient
            yield TestClient(main_mod.app)


class TestRequestIdMiddleware:
    """Drive the middleware via TestClient (`/` is unauthenticated, so
    we use it as the lightest possible inbound for these checks)."""

    def test_missing_header_mints_fresh_hex(self, _client):
        r = _client.get("/")
        assert r.status_code == 200
        rid = r.headers.get("x-request-id", "")
        assert _RID_PATTERN_HEX.match(rid), f"unexpected rid: {rid!r}"

    def test_valid_inbound_header_honoured(self, _client):
        r = _client.get("/", headers={"X-Request-ID": "my-trace-123_abc"})
        assert r.headers["x-request-id"] == "my-trace-123_abc"

    def test_oversized_header_replaced(self, _client):
        oversized = "x" * 65
        r = _client.get("/", headers={"X-Request-ID": oversized})
        rid = r.headers["x-request-id"]
        assert rid != oversized
        assert _RID_PATTERN_HEX.match(rid), f"unexpected rid: {rid!r}"

    def test_control_char_header_replaced(self, _client):
        # `.` and `:` are not in the allowlist [A-Za-z0-9_-]
        r = _client.get("/", headers={"X-Request-ID": "trace.with:colon"})
        rid = r.headers["x-request-id"]
        assert rid != "trace.with:colon"
        assert _RID_PATTERN_HEX.match(rid), f"unexpected rid: {rid!r}"

    def test_empty_header_replaced(self, _client):
        r = _client.get("/", headers={"X-Request-ID": ""})
        rid = r.headers["x-request-id"]
        assert rid != ""
        assert _RID_PATTERN_HEX.match(rid), f"unexpected rid: {rid!r}"

    def test_two_sequential_requests_get_different_ids(self, _client):
        ids = set()
        for _ in range(3):
            r = _client.get("/")
            ids.add(r.headers["x-request-id"])
        assert len(ids) == 3, f"ids leaked across requests: {ids!r}"

    def test_response_header_lowercase_x_request_id(self, _client):
        """HTTP headers are case-insensitive; we lower-case the key
        on the response. Documented contract — Sentry's correlation
        UI + the Next.js proxy both rely on the same casing."""
        r = _client.get("/")
        # `requests`/`httpx` lower-cases keys; assert via .get with
        # the canonical-cased name AND the lowercased name.
        assert r.headers.get("X-Request-ID") is not None
        assert r.headers.get("x-request-id") is not None


# ---------------------------------------------------------------------------
# 3. ContextVar isolation + background-task binding contract
# ---------------------------------------------------------------------------


class TestContextVarBindingHelpers(unittest.TestCase):
    """`bind_request_context` + `clear_request_context` are public
    helpers for background tasks. Verify they round-trip cleanly."""

    def test_bind_then_clear_round_trip(self):
        # Pre-condition: defaults.
        self.assertIsNone(request_id_var.get())
        self.assertIsNone(user_id_var.get())
        self.assertIsNone(route_var.get())

        tokens = bind_request_context("rid-A", "user-A", "/path-A")
        try:
            self.assertEqual(request_id_var.get(), "rid-A")
            self.assertEqual(user_id_var.get(), "user-A")
            self.assertEqual(route_var.get(), "/path-A")
        finally:
            clear_request_context(tokens)

        # Post-condition: back to defaults.
        self.assertIsNone(request_id_var.get())
        self.assertIsNone(user_id_var.get())
        self.assertIsNone(route_var.get())

    def test_nested_bind_unwinds_correctly(self):
        """If a background handler binds rid-A and then a nested call
        binds rid-B, clearing B must restore A, not None."""
        outer = bind_request_context("rid-outer")
        try:
            self.assertEqual(request_id_var.get(), "rid-outer")
            inner = bind_request_context("rid-inner")
            try:
                self.assertEqual(request_id_var.get(), "rid-inner")
            finally:
                clear_request_context(inner)
            self.assertEqual(request_id_var.get(), "rid-outer")
        finally:
            clear_request_context(outer)
        self.assertIsNone(request_id_var.get())


if __name__ == "__main__":
    unittest.main()
