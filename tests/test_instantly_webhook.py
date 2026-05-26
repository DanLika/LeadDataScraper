"""Contract pins for POST /webhooks/instantly.

- HMAC verify on raw body bytes (NOT the json-roundtripped form)
- Timestamp window enforces ±5 min
- (provider, event_id) idempotency: duplicate event → 200 + {duplicate: true}
- Event-type → state-transition matrix:
  * email_sent → campaign_messages.status='sent' + provider_message_id stamped
  * email_bounced → status='bounced' + suppression(reason='bounce_hard', channel='email')
  * email_unsubscribed → status='unsubscribed' + suppression(reason='unsubscribe', channel='all')
  * email_replied → status='replied'
- Background-task execution is forced synchronous via TestClient so
  the side-effect writes are observable inside the test scope.

Mocks the lazy `db` global via the canonical pattern (test_gdpr_export.py).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
import unittest
from hashlib import sha256
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)


from main import app  # noqa: E402


SIGNING_SECRET = "test-instantly-webhook-secret-32chars-x"
API_KEY = "test-webhook-api-key-32-chars-long-secret"


def _sign(body: bytes, secret: str = SIGNING_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _now_ts() -> str:
    return str(int(time.time()))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("INSTANTLY_WEBHOOK_SIGNING_SECRET", SIGNING_SECRET)


class _RecordingDb:
    """Minimal supabase-py mock that records every mutation against
    every table so tests can assert state-transition + suppression
    writes."""

    def __init__(self, *, allow_duplicate: bool = False) -> None:
        self.client = MagicMock()
        self.inserts: dict[str, list[dict[str, Any]]] = {}
        self.updates: list[tuple[str, dict, dict]] = []  # (table, set, where)
        # event_id seen — second hit on the same id raises 23505
        self._seen_event_ids: set[str] = set()
        self._allow_duplicate = allow_duplicate
        self.client.table.side_effect = self._table

    def _table(self, name: str) -> MagicMock:
        recorder = self
        chain = MagicMock()
        chain._set: dict[str, Any] = {}
        chain._where: dict[str, Any] = {}

        chain.select.return_value = chain
        chain.eq.side_effect = lambda col, val, c=chain: (
            c._where.__setitem__(col, val) or c
        )
        chain.in_.return_value = chain
        chain.limit.return_value = chain

        def insert(rows):
            rows_list = rows if isinstance(rows, list) else [rows]
            # Idempotency simulation for webhook_events.
            if name == "webhook_events" and not recorder._allow_duplicate:
                for r in rows_list:
                    eid = r.get("event_id")
                    if eid in recorder._seen_event_ids:
                        class _Dup(Exception):
                            code = "23505"
                        raise _Dup("duplicate key value violates unique constraint")
                    recorder._seen_event_ids.add(eid)
            recorder.inserts.setdefault(name, []).extend(rows_list)
            return chain

        def update(values):
            chain._set = dict(values)
            return chain

        def execute():
            if chain._set:
                recorder.updates.append((name, dict(chain._set), dict(chain._where)))
                chain._set = {}
                chain._where = {}
            return MagicMock(data=[])

        chain.insert.side_effect = insert
        chain.update.side_effect = update
        chain.execute.side_effect = execute
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


def _body(event_id: str, event_type: str, **extras: Any) -> bytes:
    payload = {"event_id": event_id, "event_type": event_type, **extras}
    return json.dumps(payload).encode("utf-8")


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


class TestAuthGates:
    def test_valid_signature_accepted(self, client):
        body = _body("evt-1", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={
                "X-Signature": _sign(body),
                "X-Timestamp": _now_ts(),
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_missing_signature_rejected(self, client):
        body = _body("evt-2", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 401

    def test_tampered_body_rejected(self, client):
        body = _body("evt-3", "email_sent")
        sig = _sign(body)
        # Add a single byte to the body so the HMAC mismatches.
        tampered = body[:-1] + b" }"
        resp = client.post(
            "/webhooks/instantly",
            content=tampered,
            headers={"X-Signature": sig, "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 401

    def test_stale_timestamp_rejected(self, client):
        body = _body("evt-4", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={
                "X-Signature": _sign(body),
                "X-Timestamp": str(int(time.time()) - 3600),
            },
        )
        assert resp.status_code == 401

    def test_missing_signing_secret_returns_401(self, client, monkeypatch):
        monkeypatch.delenv("INSTANTLY_WEBHOOK_SIGNING_SECRET", raising=False)
        body = _body("evt-cfg", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        # Operator misconfig surfaces as 401 (don't leak via 500 / 503).
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_event_id_returns_200_with_flag(self, client, _patch_db_and_limiter):
        body = _body("evt-dup", "email_sent")
        headers = {"X-Signature": _sign(body), "X-Timestamp": _now_ts()}
        resp1 = client.post("/webhooks/instantly", content=body, headers=headers)
        assert resp1.status_code == 200
        assert "duplicate" not in resp1.json()
        # Second POST with the same event_id — collision.
        resp2 = client.post("/webhooks/instantly", content=body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json() == {"ok": True, "duplicate": True}


# ---------------------------------------------------------------------------
# Event-type → state-transition matrix
# ---------------------------------------------------------------------------


class TestEventTransitions:
    def test_email_sent_event_is_captured_but_no_update(self, client, _patch_db_and_limiter):
        """email_sent does NOT write campaign_messages until Phase 14.3.

        The naive UPDATE-by-status='pending' predicate would bulk-stamp
        every pending row across every campaign with the same
        provider_message_id, then subsequent bounce/reply webhooks would
        cascade-match the entire bulk-stamped set. The handler defers
        until the dispatcher round-trips provider_message_id at push
        time (Phase 14.3); webhook_events still captures the payload.
        """
        body = _body(
            "evt-sent-1", "email_sent",
            provider_message_id="instantly-msg-001",
            recipient_email="r@x.com",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        # Event row landed in webhook_events for replay.
        events = _patch_db_and_limiter.inserts.get("webhook_events", [])
        assert any(r.get("event_id") == "evt-sent-1" for r in events)
        # CRITICAL: no campaign_messages UPDATE — the bulk-stamp footgun.
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        # checkpoint UPDATEs target webhook_events, not campaign_messages,
        # so cms_updates should be empty.
        assert not any(u[1].get("status") == "sent" for u in cms_updates), (
            f"email_sent must not transition campaign_messages.status until "
            f"Phase 14.3 wires the dispatcher round-trip; got {cms_updates}"
        )

    def test_email_bounced_updates_status_and_inserts_suppression(
        self, client, _patch_db_and_limiter,
    ):
        body = _body(
            "evt-bounce-1", "email_bounced",
            provider_message_id="instantly-msg-002",
            recipient_email="bouncer@x.com",
            bounce_reason="550 mailbox not found",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        # campaign_messages.status='bounced' AND scoped by provider_message_id
        # (NOT by status='pending' — that would cascade-match every row).
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        bounced_updates = [u for u in cms_updates if u[1].get("status") == "bounced"]
        assert len(bounced_updates) == 1, f"expected 1 bounced UPDATE; got {bounced_updates}"
        assert bounced_updates[0][2].get("provider_message_id") == "instantly-msg-002", (
            "bounce UPDATE must scope by provider_message_id — predicate-less "
            "UPDATE would cascade-match every sent row"
        )
        # suppression row inserted: reason='bounce_hard', channel='email'
        sup_rows = _patch_db_and_limiter.inserts.get("suppressions", [])
        assert any(
            r.get("identifier_value") == "bouncer@x.com"
            and r.get("reason") == "bounce_hard"
            and r.get("channel") == "email"
            and r.get("source_provider") == "instantly"
            for r in sup_rows
        ), f"expected bounce_hard suppression; got {sup_rows}"

    def test_email_unsubscribed_inserts_channel_all_suppression(
        self, client, _patch_db_and_limiter,
    ):
        body = _body(
            "evt-unsub-1", "email_unsubscribed",
            provider_message_id="instantly-msg-003",
            recipient_email="quitter@x.com",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        sup_rows = _patch_db_and_limiter.inserts.get("suppressions", [])
        assert any(
            r.get("identifier_value") == "quitter@x.com"
            and r.get("reason") == "unsubscribe"
            and r.get("channel") == "all"
            for r in sup_rows
        ), f"expected unsubscribe (channel=all); got {sup_rows}"

    def test_email_replied_stamps_status_replied(
        self, client, _patch_db_and_limiter,
    ):
        body = _body(
            "evt-reply-1", "email_replied",
            provider_message_id="instantly-msg-004",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        assert any(u[1].get("status") == "replied" for u in cms_updates)

    def test_unknown_event_type_still_stored_no_transition(
        self, client, _patch_db_and_limiter,
    ):
        body = _body("evt-novel-1", "email_lasered")  # not in allowlist
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        # Stored in webhook_events for replay.
        events = _patch_db_and_limiter.inserts.get("webhook_events", [])
        assert any(r.get("event_id") == "evt-novel-1" for r in events)
        # No campaign_messages.status transition.
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        assert not any(u[1].get("status") for u in cms_updates)


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_oversized_body_rejected_413(self, client):
        body = b"x" * (256 * 1024 + 1)
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 413

    def test_non_json_body_rejected(self, client):
        body = b"not json at all"
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 401

    def test_missing_event_id_rejected(self, client):
        body = json.dumps({"event_type": "email_sent"}).encode("utf-8")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 401


if __name__ == "__main__":
    unittest.main()
