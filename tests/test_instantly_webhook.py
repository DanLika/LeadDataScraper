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
        # `.in_("status", ["pending","sent"])` — record under a list-aware key.
        chain.in_.side_effect = lambda col, vals, c=chain: (
            c._where.__setitem__(f"{col}__in", list(vals)) or c
        )
        # `.is_("provider_message_id", "null")` — record under an is-key.
        chain.is_.side_effect = lambda col, val, c=chain: (
            c._where.__setitem__(f"{col}__is", val) or c
        )
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

    def test_duplicate_path_schedules_background_task(
        self, client, _patch_db_and_limiter,
    ):
        """Path B (2026-05-27): the duplicate path must also fire the
        background task. Otherwise an earlier delivery that returned
        500 on a transport error (row committed but response dropped)
        is unrecoverable when Instantly retries — the retry hits the
        idempotency lock and returns 200 with no state transition,
        leaving the event stranded with ``processed_at IS NULL``.
        Handlers are idempotent (mark_sent gates on
        ``provider_message_id IS NULL``), so re-firing is safe.
        """
        body = _body(
            "evt-dup-bg", "email_sent",
            provider_message_id="instantly-msg-dup",
            recipient_email="r@x.com",
            sent_at="2026-05-27T12:00:00Z",
            custom_variables={"lds_message_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        )
        headers = {"X-Signature": _sign(body), "X-Timestamp": _now_ts()}
        client.post("/webhooks/instantly", content=body, headers=headers)
        sent_after_first = sum(
            1 for u in _patch_db_and_limiter.updates
            if u[0] == "campaign_messages" and u[1].get("status") == "sent"
        )
        assert sent_after_first == 1
        resp2 = client.post("/webhooks/instantly", content=body, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json() == {"ok": True, "duplicate": True}
        sent_after_dup = sum(
            1 for u in _patch_db_and_limiter.updates
            if u[0] == "campaign_messages" and u[1].get("status") == "sent"
        )
        assert sent_after_dup == 2, (
            "duplicate path must schedule the background task; the mock "
            "doesn't enforce the .is_(provider_message_id, null) predicate "
            "so two UPDATEs land. In real DB the second is a no-op."
        )


class TestTransportRecovery:
    """Path C (2026-05-27): when supabase-py raises an httpx/httpcore
    transport error AFTER PostgREST has committed the INSERT, the
    handler re-reads ``webhook_events`` and surfaces 200 + scheduled
    background task instead of misleading 500. Verified against
    Render Cloudflare → Supabase under N=200 burst test where
    ``httpcore.RemoteProtocolError: Server disconnected`` landed at
    ~8-23% with the row already committed."""

    def test_transport_error_post_commit_re_read_finds_row_returns_200(
        self, client, _patch_db_and_limiter, monkeypatch,
    ):
        import httpx
        import main as backend_main
        from src.repositories.webhook_event_repo import (
            InsertResult, WebhookEventRepository,
        )

        async def fake_insert(self, **kwargs):
            raise httpx.RemoteProtocolError("Server disconnected")

        async def fake_exists(provider: str, event_id: str) -> bool:
            return True

        monkeypatch.setattr(
            WebhookEventRepository, "insert_event", fake_insert, raising=True,
        )
        monkeypatch.setattr(
            backend_main, "_webhook_event_exists", fake_exists, raising=True,
        )

        body = _body("evt-transport-1", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "recovered": True}

    def test_transport_error_pre_commit_no_row_returns_500(
        self, client, _patch_db_and_limiter, monkeypatch,
    ):
        import httpx
        import main as backend_main
        from src.repositories.webhook_event_repo import WebhookEventRepository

        async def fake_insert(self, **kwargs):
            raise httpx.RemoteProtocolError("Server disconnected")

        async def fake_exists(provider: str, event_id: str) -> bool:
            return False

        monkeypatch.setattr(
            WebhookEventRepository, "insert_event", fake_insert, raising=True,
        )
        monkeypatch.setattr(
            backend_main, "_webhook_event_exists", fake_exists, raising=True,
        )

        body = _body("evt-transport-2", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 500
        assert resp.json() == {"detail": "internal error"}

    def test_non_transport_error_unchanged_returns_500(
        self, client, _patch_db_and_limiter, monkeypatch,
    ):
        """Non-transport errors (e.g., a real PostgREST APIError that
        is NOT a 23505) must still surface as 500. The re-read path
        is only the recovery for transport-class errors."""
        import main as backend_main
        from src.repositories.webhook_event_repo import WebhookEventRepository

        async def fake_insert(self, **kwargs):
            raise RuntimeError("non-transport DB error")

        called = {"exists": 0}

        async def fake_exists(provider: str, event_id: str) -> bool:
            called["exists"] += 1
            return True  # would mask the bug if branch fires; assert it doesn't

        monkeypatch.setattr(
            WebhookEventRepository, "insert_event", fake_insert, raising=True,
        )
        monkeypatch.setattr(
            backend_main, "_webhook_event_exists", fake_exists, raising=True,
        )

        body = _body("evt-non-transport", "email_sent")
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 500
        assert called["exists"] == 0, "re-read should only run for transport-class errors"


# ---------------------------------------------------------------------------
# Event-type → state-transition matrix
# ---------------------------------------------------------------------------


class TestEventTransitions:
    def test_email_sent_with_lds_message_id_stamps_provider_message_id(
        self, client, _patch_db_and_limiter,
    ):
        """Phase 14.3: email_sent does a first-hit-wins UPDATE targeting
        ``id = lds_message_id`` with ``provider_message_id IS NULL``
        predicate. Replays match zero rows and no-op."""
        body = _body(
            "evt-sent-1", "email_sent",
            provider_message_id="instantly-msg-001",
            recipient_email="r@x.com",
            sent_at="2026-05-25T10:00:00Z",
            custom_variables={"lds_message_id": "11111111-2222-3333-4444-555555555555"},
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        sent_updates = [u for u in cms_updates if u[1].get("status") == "sent"]
        assert len(sent_updates) == 1, f"expected 1 sent UPDATE; got {sent_updates}"
        _, set_clause, where = sent_updates[0]
        # SET clause: provider_message_id + status + sent_at
        assert set_clause["provider_message_id"] == "instantly-msg-001"
        assert set_clause["sent_at"] == "2026-05-25T10:00:00Z"
        # Predicate: id = lds_message_id AND provider_message_id IS NULL
        # (first-hit-wins replay guard)
        assert where["id"] == "11111111-2222-3333-4444-555555555555"
        assert where.get("provider_message_id__is") == "null"

    def test_email_sent_without_lds_message_id_is_legacy_no_op(
        self, client, _patch_db_and_limiter,
    ):
        """Legacy / pre-14.3 events without custom_variables.lds_message_id
        cannot identify the row — handler logs + skips the UPDATE rather
        than fall back to the pre-14.3 bulk-stamp footgun."""
        body = _body(
            "evt-sent-legacy", "email_sent",
            provider_message_id="instantly-msg-legacy",
            recipient_email="r@x.com",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        # Event captured for replay; no campaign_messages.status='sent' UPDATE.
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        assert not any(u[1].get("status") == "sent" for u in cms_updates), (
            f"legacy email_sent without lds_message_id must be a no-op; got {cms_updates}"
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
        replied_updates = [u for u in cms_updates if u[1].get("status") == "replied"]
        assert len(replied_updates) == 1
        # Phase 14.3: reply UPDATE enforces state-machine `sent → replied`.
        assert replied_updates[0][2].get("status__in") == ["sent"]

    def test_race_bounce_before_sent_still_inserts_suppression(
        self, client, _patch_db_and_limiter,
    ):
        """Documented race: Instantly's background workers don't guarantee
        ordering between email_sent and email_bounced. If bounce arrives
        first, the row's provider_message_id is still NULL and the
        campaign_messages UPDATE matches zero rows. Suppression INSERT
        still fires via recipient_email — that's the load-bearing
        defense for the next-send cycle."""
        body = _body(
            "evt-bounce-orphan", "email_bounced",
            provider_message_id="instantly-msg-orphan",
            recipient_email="orphan-bounce@x.com",
            bounce_reason="550 unreachable",
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200
        # The UPDATE call was issued (predicate is what determines the
        # 0-row match, not whether we asked); for the mock it lands in
        # updates list either way. The KEY assertion is the suppression
        # INSERT fires on recipient_email regardless.
        sup_rows = _patch_db_and_limiter.inserts.get("suppressions", [])
        assert any(
            r.get("identifier_value") == "orphan-bounce@x.com"
            and r.get("reason") == "bounce_hard"
            for r in sup_rows
        ), "suppression must INSERT via recipient_email even on race"

    def test_email_sent_replay_uses_first_hit_wins_predicate(
        self, client, _patch_db_and_limiter,
    ):
        """Replay coverage: same lds_message_id seen twice. Both webhook
        deliveries reach the handler (idempotency on event_id collapses
        only at webhook_events; the side-effects path runs each time)."""
        common_msg_id = "22222222-3333-4444-5555-666666666666"
        body1 = _body(
            "evt-sent-replay-1", "email_sent",
            provider_message_id="msg-A",
            custom_variables={"lds_message_id": common_msg_id},
        )
        body2 = _body(
            "evt-sent-replay-2", "email_sent",
            provider_message_id="msg-A",
            custom_variables={"lds_message_id": common_msg_id},
        )
        for b in (body1, body2):
            resp = client.post(
                "/webhooks/instantly",
                content=b,
                headers={"X-Signature": _sign(b), "X-Timestamp": _now_ts()},
            )
            assert resp.status_code == 200
        cms_updates = [u for u in _patch_db_and_limiter.updates if u[0] == "campaign_messages"]
        sent_updates = [u for u in cms_updates if u[1].get("status") == "sent"]
        # Each call generates one UPDATE (mock can't enforce predicate-
        # based zero-row no-op without simulating row state). The KEY
        # assertion is the predicate carries the IS NULL guard so a
        # REAL Postgres execute would match zero rows on the second call.
        assert len(sent_updates) == 2
        for _, _, where in sent_updates:
            assert where.get("provider_message_id__is") == "null", (
                "every email_sent UPDATE must carry the IS NULL replay guard"
            )

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
# Transport-error mid-processing (Issue #368)
# ---------------------------------------------------------------------------


class TestTransportErrorMidProcessing:
    """Issue #368: when a handler hits an httpx/httpcore transport error
    DURING side-effect work (e.g. supabase-py drops the response on a
    ``campaign_messages`` UPDATE), the row in ``webhook_events`` must
    keep ``processed_at NULL`` so the sweeper's
    ``idx_webhook_events_unprocessed`` scan re-claims it.

    Pre-fix behavior: the broad ``except Exception`` always stamped
    ``processed_at`` + ``processing_error``, marking the event "done"
    while the side-effect may or may not have committed → permanent
    state loss.

    Distinction pinned here:
      * Transport-class exception → leave ``processed_at`` NULL.
        Handlers are predicate-idempotent, so sweeper re-fire is safe
        even when the original write DID commit.
      * Genuine handler-logic exception → still stamp ``processed_at``
        + ``processing_error``. Poison messages must not loop.
    """

    def test_transport_error_during_handler_leaves_processed_at_null(
        self, client, _patch_db_and_limiter, monkeypatch,
    ):
        import httpx
        import main as backend_main

        async def boom(*_args, **_kwargs):
            raise httpx.RemoteProtocolError("Server disconnected mid-UPDATE")

        monkeypatch.setattr(
            backend_main, "_instantly_handle_sent", boom, raising=True,
        )

        body = _body(
            "evt-transport-proc", "email_sent",
            provider_message_id="instantly-msg-tp",
            recipient_email="r@x.com",
            sent_at="2026-05-27T12:00:00Z",
            custom_variables={"lds_message_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        # Inbound INSERT succeeds; BackgroundTask runs in TestClient.
        assert resp.status_code == 200

        webhook_updates = [
            u for u in _patch_db_and_limiter.updates
            if u[0] == "webhook_events"
        ]
        # The checkpoint UPDATE on webhook_events MUST be skipped for
        # transport-class errors. Any update stamping processed_at
        # would mask the row from the sweeper.
        assert not any(
            "processed_at" in u[1] for u in webhook_updates
        ), (
            "transport error mid-processing must NOT stamp processed_at; "
            f"got updates={webhook_updates}"
        )

    def test_non_transport_error_still_checkpoints_processed_at(
        self, client, _patch_db_and_limiter, monkeypatch,
    ):
        """Counter-pin: genuine handler bug (poison message) MUST still
        checkpoint, otherwise it would loop forever through the sweeper."""
        import main as backend_main

        async def boom(*_args, **_kwargs):
            raise ValueError("malformed payload field")

        monkeypatch.setattr(
            backend_main, "_instantly_handle_sent", boom, raising=True,
        )

        body = _body(
            "evt-poison-1", "email_sent",
            provider_message_id="instantly-msg-poison",
            recipient_email="r@x.com",
            custom_variables={"lds_message_id": "11111111-2222-3333-4444-555555555555"},
        )
        resp = client.post(
            "/webhooks/instantly",
            content=body,
            headers={"X-Signature": _sign(body), "X-Timestamp": _now_ts()},
        )
        assert resp.status_code == 200

        webhook_updates = [
            u for u in _patch_db_and_limiter.updates
            if u[0] == "webhook_events"
        ]
        stamping = [u for u in webhook_updates if "processed_at" in u[1]]
        assert len(stamping) == 1, (
            f"non-transport handler error must still checkpoint; got {webhook_updates}"
        )
        # processing_error captured so the row carries the poison cause.
        assert "ValueError" in (stamping[0][1].get("processing_error") or "")

    @pytest.mark.asyncio
    async def test_sweeper_reclaims_unstamped_row_after_transport_error(
        self, monkeypatch,
    ):
        """End-to-end: a row left ``processed_at IS NULL`` by the
        transport-error path is what the sweeper's
        ``.is_("processed_at", "null")`` predicate picks up. Pins that
        the sweeper actually re-fires ``_process_instantly_event`` for
        such rows so the side-effect gets another shot."""
        from datetime import datetime, timedelta, timezone
        from src.workers.webhook_sweeper import sweep_once

        # Row simulating a transport-stranded webhook_events entry:
        # past the grace window + processed_at NULL.
        stranded = {
            "id": 42,
            "provider": "instantly",
            "event_id": "evt-transport-stranded",
            "event_type": "email_sent",
            "payload": {
                "event_id": "evt-transport-stranded",
                "event_type": "email_sent",
                "recipient_email": "r@x.com",
                "custom_variables": {"lds_message_id": "ccc"},
            },
            "received_at": (
                datetime.now(timezone.utc) - timedelta(seconds=180)
            ).isoformat(),
        }

        chain = MagicMock()
        chain.select.return_value = chain
        captured: dict[str, Any] = {}

        def _is(col, val, c=chain):
            captured.setdefault("is_", []).append((col, val))
            return c

        def _lt(col, val, c=chain):
            captured.setdefault("lt", []).append((col, val))
            return c

        chain.is_.side_effect = _is
        chain.lt.side_effect = _lt
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=[stranded])

        db = MagicMock()
        db.client = MagicMock()
        db.client.table.return_value = chain

        recorder: list[tuple[str, dict]] = []

        async def fake_proc(*, event_id: str, payload: dict) -> None:
            recorder.append((event_id, dict(payload)))

        result = await sweep_once(
            db=db,
            process_instantly_event=fake_proc,
            grace_seconds=60,
        )

        # Sweeper's claim predicate matched ``processed_at IS NULL``.
        assert ("processed_at", "null") in captured.get("is_", []), (
            f"sweeper must filter by processed_at IS NULL; got {captured}"
        )
        # Stranded row dispatched back to the handler.
        assert result.scanned == 1
        assert result.processed == 1
        assert recorder == [("evt-transport-stranded", stranded["payload"])]


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
