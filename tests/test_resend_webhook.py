"""Contract pins for POST /webhooks/resend (Phase 16 T2 stub).

Asserts:

- Svix HMAC verify on raw body + svix-id + svix-timestamp.
- (provider, event_id=svix-id) idempotency: duplicate event → 200 +
  {duplicate: true} + handler dispatch still fires (recovery path).
- _RESEND_HANDLED_EVENTS routing: handled types schedule
  _process_resend_event; unhandled types ack 200 without dispatch.
- email.replied with the right custom_args calls
  ReplyClassifierService.handle_replied_event with the expected args.
- email.replied with PHASE16_REPLY_CLASSIFIER=0 (default) acks +
  short-circuits inside the service (no Anthropic call attempted).
- email.replied missing lds_lead_unique_key custom_arg logs warning
  and does NOT call into the service.

Mocks the lazy `db` global via the canonical pattern from
test_instantly_webhook.py.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import time
import unittest
from hashlib import sha256
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


# Same shape as test_instantly_webhook: keep secret + API key valid-looking
# (32+ chars) so no length validation rejects them out of hand.
_RAW_SVIX_SECRET = b"phase16-resend-test-secret-bytes32"
SVIX_SECRET = "whsec_" + base64.b64encode(_RAW_SVIX_SECRET).decode("ascii")
API_KEY = "test-webhook-api-key-32-chars-long-secret"


def _svix_sign(svix_id: str, ts: str, body: bytes, secret_bytes: bytes = _RAW_SVIX_SECRET) -> str:
    msg = f"{svix_id}.{ts}.".encode("utf-8") + body
    return "v1," + base64.b64encode(hmac.new(secret_bytes, msg, sha256).digest()).decode("ascii")


def _now_ts() -> str:
    return str(int(time.time()))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("RESEND_WEBHOOK_SIGNING_SECRET", SVIX_SECRET)
    # PHASE16_REPLY_CLASSIFIER intentionally LEFT UNSET — default-off
    # is what production runs while T2 stub PR lives on the branch.
    monkeypatch.delenv("PHASE16_REPLY_CLASSIFIER", raising=False)


class _RecordingDb:
    def __init__(self, *, allow_duplicate: bool = False) -> None:
        self.client = MagicMock()
        self.inserts: dict[str, list[dict[str, Any]]] = {}
        self._seen_event_ids: set[tuple[str, str]] = set()
        self._allow_duplicate = allow_duplicate
        self.client.table.side_effect = self._table

    def _table(self, name: str) -> MagicMock:
        recorder = self
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.limit.return_value = chain

        def insert(rows):
            rows_list = rows if isinstance(rows, list) else [rows]
            if name == "webhook_events" and not recorder._allow_duplicate:
                for r in rows_list:
                    key = (r.get("provider"), r.get("event_id"))
                    if key in recorder._seen_event_ids:

                        class _Dup(Exception):
                            code = "23505"

                        raise _Dup("duplicate key value violates unique constraint")
                    recorder._seen_event_ids.add(key)
            recorder.inserts.setdefault(name, []).extend(rows_list)
            return chain

        chain.insert.side_effect = insert
        chain.update.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        return chain


@pytest.fixture(autouse=True)
def _patch_db_and_limiter():
    import main

    try:
        main.limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            main.limiter.reset()
        except Exception:  # noqa: BLE001
            pass
    main.db = _RecordingDb()
    main.router = MagicMock()
    main.auditor = MagicMock()
    main.orchestrator = MagicMock()
    yield main.db


@pytest.fixture
def client():
    return TestClient(app)


def _body(type_: str, **extras: Any) -> bytes:
    payload = {"type": type_, "data": extras.pop("data", {}), **extras}
    return json.dumps(payload).encode("utf-8")


# ---------------------------------------------------------------------------
# Svix auth gates
# ---------------------------------------------------------------------------


class TestSvixAuth:
    def test_valid_signature_accepted(self, client):
        body = _body("email.delivered", data={"id": "rs_001"})
        ts = _now_ts()
        sig = _svix_sign("msg_001", ts, body)
        resp = client.post(
            "/webhooks/resend",
            content=body,
            headers={
                "svix-id": "msg_001",
                "svix-timestamp": ts,
                "svix-signature": sig,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}

    def test_missing_signature_rejected(self, client):
        body = _body("email.delivered")
        resp = client.post(
            "/webhooks/resend",
            content=body,
            headers={
                "svix-id": "msg_002",
                "svix-timestamp": _now_ts(),
            },
        )
        assert resp.status_code == 401

    def test_tampered_body_rejected(self, client):
        body = _body("email.delivered")
        ts = _now_ts()
        sig = _svix_sign("msg_003", ts, body)
        tampered = _body("email.bounced")  # different bytes
        resp = client.post(
            "/webhooks/resend",
            content=tampered,
            headers={
                "svix-id": "msg_003",
                "svix-timestamp": ts,
                "svix-signature": sig,
            },
        )
        assert resp.status_code == 401

    def test_stale_timestamp_rejected(self, client):
        body = _body("email.delivered")
        ts = str(int(time.time()) - 3600)
        sig = _svix_sign("msg_004", ts, body)
        resp = client.post(
            "/webhooks/resend",
            content=body,
            headers={
                "svix-id": "msg_004",
                "svix-timestamp": ts,
                "svix-signature": sig,
            },
        )
        assert resp.status_code == 401

    def test_missing_secret_env_rejected(self, client, monkeypatch):
        monkeypatch.delenv("RESEND_WEBHOOK_SIGNING_SECRET", raising=False)
        body = _body("email.delivered")
        ts = _now_ts()
        resp = client.post(
            "/webhooks/resend",
            content=body,
            headers={
                "svix-id": "x",
                "svix-timestamp": ts,
                "svix-signature": _svix_sign("x", ts, body),
            },
        )
        assert resp.status_code == 401

    def test_payload_size_cap_413(self, client):
        # 257 KB > 256 KB cap.
        big = b"x" * (257 * 1024)
        ts = _now_ts()
        resp = client.post(
            "/webhooks/resend",
            content=big,
            headers={
                "svix-id": "msg_oversize",
                "svix-timestamp": ts,
                "svix-signature": _svix_sign("msg_oversize", ts, big),
            },
        )
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Event-type dispatch routing
# ---------------------------------------------------------------------------


class TestEventDispatch:
    @pytest.mark.parametrize(
        "handled_type",
        [
            "email.sent",
            "email.delivered",
            "email.delivery_delayed",
            "email.bounced",
            "email.complained",
            "email.opened",
            "email.clicked",
            "email.replied",
        ],
    )
    def test_handled_types_schedule_background(self, client, handled_type):
        body = _body(handled_type, data={"id": f"rs_{handled_type}"})
        ts = _now_ts()
        sig = _svix_sign(f"msg_{handled_type}", ts, body)
        with patch("main._process_resend_event", new=AsyncMock()) as mocked:
            resp = client.post(
                "/webhooks/resend",
                content=body,
                headers={
                    "svix-id": f"msg_{handled_type}",
                    "svix-timestamp": ts,
                    "svix-signature": sig,
                },
            )
        assert resp.status_code == 200
        # BackgroundTasks runs the task synchronously when using TestClient
        # (Starlette TestClient awaits the task before returning).
        assert mocked.await_count == 1
        kwargs = mocked.await_args.kwargs
        assert kwargs["event_id"] == f"msg_{handled_type}"
        assert kwargs["payload"]["type"] == handled_type

    def test_unhandled_type_acks_without_dispatch(self, client):
        body = _body("email.unsupported_future_type")
        ts = _now_ts()
        sig = _svix_sign("msg_unh", ts, body)
        with patch("main._process_resend_event", new=AsyncMock()) as mocked:
            resp = client.post(
                "/webhooks/resend",
                content=body,
                headers={
                    "svix-id": "msg_unh",
                    "svix-timestamp": ts,
                    "svix-signature": sig,
                },
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mocked.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency (svix-id is the unique key)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_event_returns_duplicate_flag(self, client):
        body = _body("email.delivered", data={"id": "rs_dup"})
        ts = _now_ts()
        sig = _svix_sign("msg_dup", ts, body)
        headers = {
            "svix-id": "msg_dup",
            "svix-timestamp": ts,
            "svix-signature": sig,
        }
        first = client.post("/webhooks/resend", content=body, headers=headers)
        second = client.post("/webhooks/resend", content=body, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json().get("duplicate") is True


# ---------------------------------------------------------------------------
# email.replied → classifier service routing
# ---------------------------------------------------------------------------


class TestRepliedRouting:
    def test_replied_calls_service_with_extracted_fields(self, client):
        body = _body(
            "email.replied",
            data={
                "id": "rs_reply_1",
                "text": "Yes please, let's schedule a call.",
                "custom_args": {
                    "lds_lead_unique_key": "acme.com_alice@acme.com",
                    "lds_message_id": "lds-msg-42",
                },
            },
        )
        ts = _now_ts()
        sig = _svix_sign("msg_reply_1", ts, body)

        # Mock the service constructor — we want to inspect the call args
        # that flow from _process_resend_event into the service method.
        with patch(
            "src.services.reply_classifier_service.ReplyClassifierService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.handle_replied_event = AsyncMock(return_value=None)
            resp = client.post(
                "/webhooks/resend",
                content=body,
                headers={
                    "svix-id": "msg_reply_1",
                    "svix-timestamp": ts,
                    "svix-signature": sig,
                },
            )

        assert resp.status_code == 200
        instance.handle_replied_event.assert_awaited_once()
        kwargs = instance.handle_replied_event.await_args.kwargs
        assert kwargs["reply_body"] == "Yes please, let's schedule a call."
        assert kwargs["lead_unique_key"] == "acme.com_alice@acme.com"
        assert kwargs["campaign_message_id"] == "lds-msg-42"
        assert kwargs["provider_event_id"] == "msg_reply_1"

    def test_replied_default_disabled_skips_anthropic_via_service(self, client):
        """End-to-end: with PHASE16_REPLY_CLASSIFIER unset (default 0),
        the real service is constructed but handle_replied_event
        returns None after preprocessing without calling Anthropic.
        The test patches classify_via_anthropic to assert non-call.
        """
        body = _body(
            "email.replied",
            data={
                "id": "rs_reply_2",
                "text": "Sounds great, let's chat next week.",
                "custom_args": {
                    "lds_lead_unique_key": "acme.com_bob@acme.com",
                },
            },
        )
        ts = _now_ts()
        sig = _svix_sign("msg_reply_2", ts, body)
        with patch(
            "src.services.reply_classifier_service.ReplyClassifierService."
            "classify_via_anthropic",
            new=AsyncMock(side_effect=AssertionError("should not be called when disabled")),
        ) as mocked_classify:
            resp = client.post(
                "/webhooks/resend",
                content=body,
                headers={
                    "svix-id": "msg_reply_2",
                    "svix-timestamp": ts,
                    "svix-signature": sig,
                },
            )
        assert resp.status_code == 200
        mocked_classify.assert_not_called()

    def test_replied_missing_lead_key_logs_and_skips(self, client, caplog):
        body = _body(
            "email.replied",
            data={
                "id": "rs_reply_3",
                "text": "hi",
                # NO custom_args.lds_lead_unique_key → cannot classify.
            },
        )
        ts = _now_ts()
        sig = _svix_sign("msg_reply_3", ts, body)
        with patch(
            "src.services.reply_classifier_service.ReplyClassifierService"
        ) as MockSvc:
            instance = MockSvc.return_value
            instance.handle_replied_event = AsyncMock(side_effect=AssertionError("no key"))
            with caplog.at_level("WARNING", logger="main"):
                resp = client.post(
                    "/webhooks/resend",
                    content=body,
                    headers={
                        "svix-id": "msg_reply_3",
                        "svix-timestamp": ts,
                        "svix-signature": sig,
                    },
                )
        assert resp.status_code == 200
        instance.handle_replied_event.assert_not_called()
        warn_msgs = [r.message for r in caplog.records]
        assert any("missing lds_lead_unique_key" in m for m in warn_msgs), warn_msgs


if __name__ == "__main__":
    unittest.main()
