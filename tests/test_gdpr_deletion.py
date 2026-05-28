"""GDPR Article 17 (right to erasure) endpoint contract pins.

``DELETE /operator/account`` is destructive and irreversible. The
endpoint must:

1. **Three-factor gate** — X-API-Key + X-Admin-Token + JSON body with
   confirmation phrase ``"DELETE MY ACCOUNT"`` (Pydantic Literal). Any
   single gate failing must short-circuit BEFORE the destructive step.

2. **Audit-first invariant** — write the `account_deletions` row
   BEFORE any DELETE runs. If the audit write fails, return 503 and
   skip the destructive step entirely. A partial-failure on the
   deletion side is acceptable (FK ordering covers most cases); a
   silent deletion with no audit is not.

3. **Row counts snapshot** — pre-deletion counts captured into the
   audit row's `row_counts` JSON so a contested deletion can be
   reconstructed from "what was wiped, when, by whom, from where."

4. **Rate limit 1/hour** keyed on peer IP (NOT XFF) — same hardening
   as `/operator/data-export` against XFF-rotation bypass.

5. **30-day retention** — audit row's `expires_at` is 30 days after
   `deleted_at`. The daily purge script enforces.

Mocked DB. Pattern matches tests/test_gdpr_export.py +
tests/test_error_message_leak.py.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


API_KEY = "test-gdpr-delete-secret-key-32-chars-long"
ADMIN_TOKEN = "test-admin-token-gdpr-delete"
HEADERS_OK = {
    "X-API-Key": API_KEY,
    "X-Admin-Token": ADMIN_TOKEN,
    "Content-Type": "application/json",
}
GOOD_BODY = json.dumps({"confirmation": "DELETE MY ACCOUNT"})


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _build_mock_db(
    counts: dict | None = None,
    insert_should_fail: bool = False,
) -> MagicMock:
    """Mock SupabaseHelper.client that:
    - returns `count` on .select(..., count='exact').execute()
    - records every .insert() call (audit trail)
    - records every .delete().neq(...).execute() call (deletion trail)
    - raises on .insert if insert_should_fail
    """
    counts = counts or {}
    db = MagicMock()
    db.insert_calls = []  # populated below for assertions
    db.delete_calls = []

    def table_side_effect(table_name):
        chain = MagicMock()

        # SELECT count=exact path
        def select_side_effect(*args, **kwargs):
            sel_chain = MagicMock()
            sel_chain.execute.return_value = MagicMock(
                data=[], count=counts.get(table_name, 0)
            )
            return sel_chain

        chain.select.side_effect = select_side_effect

        # INSERT path (for account_deletions)
        def insert_side_effect(row):
            db.insert_calls.append({"table": table_name, "row": row})
            if insert_should_fail and table_name == "account_deletions":
                raise RuntimeError("simulated audit write failure")
            ins_chain = MagicMock()
            ins_chain.execute.return_value = MagicMock(data=[row])
            return ins_chain

        chain.insert.side_effect = insert_side_effect

        # DELETE.neq().execute() path
        def delete_side_effect():
            del_chain = MagicMock()

            def neq_side_effect(key_col, sentinel):
                db.delete_calls.append(
                    {
                        "table": table_name,
                        "key_col": key_col,
                        "sentinel": sentinel,
                    }
                )
                neq_chain = MagicMock()
                neq_chain.execute.return_value = MagicMock(data=[])
                return neq_chain

            del_chain.neq.side_effect = neq_side_effect
            return del_chain

        chain.delete.side_effect = delete_side_effect

        return chain

    db.client.table.side_effect = table_side_effect
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("OPERATOR_EMAIL", "operator@example.com")


@pytest.fixture(autouse=True)
def _reset_rate_limiter_and_db():
    import main

    try:
        main.limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:
        try:
            main.limiter.reset()
        except Exception:
            pass
    # Default mock — 5 leads, 2 campaigns, 3 messages, 1 job.
    main.db = _build_mock_db(
        counts={
            "leads": 5,
            "campaigns": 2,
            "campaign_messages": 3,
            "orchestration_jobs": 1,
        }
    )
    main.router = MagicMock()
    main.auditor = MagicMock()
    main.orchestrator = MagicMock()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Three-factor gate — each gate must reject independently
# ---------------------------------------------------------------------------


class TestAuthGates:
    def test_missing_api_key_returns_403(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers={"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"},
            content=GOOD_BODY,
        )
        assert r.status_code == 403

    def test_missing_admin_token_returns_403(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            content=GOOD_BODY,
        )
        assert r.status_code == 403

    def test_wrong_admin_token_returns_403(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers={
                "X-API-Key": API_KEY,
                "X-Admin-Token": "wrong-admin-token",
                "Content-Type": "application/json",
            },
            content=GOOD_BODY,
        )
        assert r.status_code == 403

    def test_wrong_confirmation_phrase_returns_422(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers=HEADERS_OK,
            content=json.dumps({"confirmation": "delete my account"}),  # wrong casing
        )
        assert r.status_code == 422

    def test_missing_confirmation_field_returns_422(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers=HEADERS_OK,
            content=json.dumps({}),
        )
        assert r.status_code == 422

    def test_extra_field_rejected_by_pydantic(self, client):
        r = client.request(
            "DELETE",
            "/operator/account",
            headers=HEADERS_OK,
            content=json.dumps(
                {
                    "confirmation": "DELETE MY ACCOUNT",
                    "bypass": True,
                }
            ),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 2. Audit-first invariant
# ---------------------------------------------------------------------------


class TestAuditFirstInvariant:
    def test_happy_path_writes_audit_then_deletes(self, client):
        import main

        r = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "deleted"
        assert "audit_id" in body
        # Audit insert happened BEFORE delete chain
        assert len(main.db.insert_calls) == 1
        assert main.db.insert_calls[0]["table"] == "account_deletions"
        # 4 deletes in FK dependency order
        delete_tables = [c["table"] for c in main.db.delete_calls]
        assert delete_tables == [
            "campaign_messages",
            "campaigns",
            "orchestration_jobs",
            "leads",
        ]

    def test_audit_write_failure_aborts_deletion(self, client):
        import main

        main.db = _build_mock_db(
            counts={"leads": 5},
            insert_should_fail=True,
        )
        r = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r.status_code == 503
        # CRITICAL: zero deletes ran because the audit row didn't land.
        assert len(main.db.delete_calls) == 0


# ---------------------------------------------------------------------------
# 3. Row counts snapshot
# ---------------------------------------------------------------------------


class TestRowCountsSnapshot:
    def test_response_includes_pre_deletion_counts(self, client):
        r = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r.status_code == 200
        body = r.json()
        rc = body["row_counts_deleted"]
        # Matches the default fixture in _reset_rate_limiter_and_db.
        assert rc == {
            "leads": 5,
            "campaigns": 2,
            "campaign_messages": 3,
            "orchestration_jobs": 1,
        }

    def test_audit_row_carries_row_counts(self, client):
        import main

        client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        audit_row = main.db.insert_calls[0]["row"]
        assert audit_row["row_counts"] == {
            "leads": 5,
            "campaigns": 2,
            "campaign_messages": 3,
            "orchestration_jobs": 1,
        }

    def test_audit_row_carries_operator_email_from_env(self, client):
        import main

        client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        audit_row = main.db.insert_calls[0]["row"]
        assert audit_row["operator_email"] == "operator@example.com"

    def test_audit_row_carries_remote_ip(self, client):
        import main

        client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        audit_row = main.db.insert_calls[0]["row"]
        # TestClient uses 'testclient' as the synthetic peer.
        assert audit_row["remote_ip"] is not None


# ---------------------------------------------------------------------------
# 4. Rate limit (1/hour, peer-IP keyed)
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_second_call_within_hour_returns_429(self, client):
        r1 = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r1.status_code == 200, r1.text
        r2 = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r2.status_code == 429, (
            f"second call should 429, got {r2.status_code}: {r2.text}"
        )


# ---------------------------------------------------------------------------
# 5. 30-day retention metadata
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestRetention:
    def test_audit_expires_30_days_after_deletion(self, client):
        from datetime import datetime, timezone
        import main

        before = datetime.now(timezone.utc)
        r = client.request(
            "DELETE",
            "/operator/account",
            headers=HEADERS_OK,
            content=GOOD_BODY,
        )
        # Status-check + insert-presence diagnostics so a real test-isolation
        # leak (mock from a prior test bleeding through) fails fast with a
        # clear message instead of silently producing a wrong delta_days.
        assert r.status_code == 200, r.text
        assert main.db.insert_calls, "no audit row written"
        audit_row = main.db.insert_calls[0]["row"]
        # The default fixture in `_reset_rate_limiter_and_db` writes the
        # `leads: 5` row count. If we read a different value here, this
        # `insert_calls[0]` is stale — the autouse fixture didn't rebuild
        # the mock as expected.
        assert audit_row["row_counts"]["leads"] == 5, (
            f"audit row from another test (leaked fixture); "
            f"got row_counts={audit_row['row_counts']}"
        )

        deleted_at = datetime.fromisoformat(
            audit_row["deleted_at"].replace("Z", "+00:00")
        )
        expires_at = datetime.fromisoformat(
            audit_row["expires_at"].replace("Z", "+00:00")
        )
        delta_days = (expires_at - deleted_at).total_seconds() / 86400.0
        # The handler captures `now` ONCE, then computes both timestamps
        # from it (deleted_at = now, expires_at = now + 30d). After ms
        # truncation, delta is EXACTLY 30.0 days. Tight tolerance catches
        # any future refactor that splits the two now() calls.
        assert 29.999 < delta_days < 30.001, f"expected exact 30 days, got {delta_days}"
        # deleted_at is in the request window.
        assert deleted_at >= before

    def test_response_payload_includes_retention_fields(self, client):
        r = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        body = r.json()
        assert body["audit_retention_days"] == 30
        assert body["audit_expires_at"].endswith("Z")


# ---------------------------------------------------------------------------
# 6. DB unavailable
# ---------------------------------------------------------------------------


class TestDbUnavailable:
    def test_503_when_db_client_is_none(self, client):
        import main

        main.db = MagicMock()
        main.db.client = None
        r = client.request(
            "DELETE", "/operator/account", headers=HEADERS_OK, content=GOOD_BODY
        )
        assert r.status_code == 503


if __name__ == "__main__":
    unittest.main()
