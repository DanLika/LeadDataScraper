"""RFC 8058 unsubscribe endpoint contract pins.

GET /unsubscribe/{token}  — renders HTML confirmation form, 200 on any
                            string-shaped token, 410 on empty/over-200ch.
POST /unsubscribe/{token} — verifies HMAC, dereferences tracking_id →
                            lead.email, inserts suppression row,
                            returns 200 on success / 410 on any failure.

Tests mock the lazy-singleton `db`, `router`, etc. via the canonical
``main.<name> = MagicMock(...)`` pattern (see test_gdpr_export.py).
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)


from main import app  # noqa: E402

API_KEY = "test-unsubscribe-32-chars-long-secret-x"
SAMPLE_TRACKING_ID = "abcdef12-3456-7890-abcd-ef1234567890"
SAMPLE_SECRET = "test-unsubscribe-signing-secret"


# ---------------------------------------------------------------------------
# Mock DB — campaign_messages lookup by tracking_id returns 1 row,
# leads lookup by unique_key returns 1 row with an email.
# ---------------------------------------------------------------------------


def _build_mock_db(*, tracking_id_found: bool = True, lead_email: str | None = "victim@example.com") -> MagicMock:
    db = MagicMock()

    def table_side_effect(table_name: str) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.limit.return_value = chain
        chain.insert.return_value = chain
        chain.upsert.return_value = chain
        if table_name == "campaign_messages" and tracking_id_found:
            chain.execute.return_value = MagicMock(data=[{
                "campaign_id": "camp-1",
                "lead_unique_key": "lead-1",
            }])
        elif table_name == "leads" and lead_email is not None:
            chain.execute.return_value = MagicMock(data=[{"email": lead_email}])
        elif table_name == "suppressions":
            chain.execute.return_value = MagicMock(data=[{"id": 999}])
        else:
            chain.execute.return_value = MagicMock(data=[])
        return chain

    db.client.table.side_effect = table_side_effect
    return db


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("UNSUBSCRIBE_TOKEN_SECRET", SAMPLE_SECRET)


@pytest.fixture(autouse=True)
def _reset_lazy_globals():
    import main
    try:
        main.limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            main.limiter.reset()
        except Exception:  # noqa: BLE001
            pass
    main.db = _build_mock_db()
    main.router = MagicMock()
    main.auditor = MagicMock()
    main.orchestrator = MagicMock()
    yield


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET — confirmation page
# ---------------------------------------------------------------------------


class TestGetEndpoint:
    def test_renders_confirm_form_for_any_token_string(self, client):
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        resp = client.get(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert "Confirm unsubscribe" in resp.text
        assert 'method="post"' in resp.text

    def test_over_200_char_token_returns_410(self, client):
        # 201 chars — beyond the bound check before verification.
        long_token = "A" * 201
        resp = client.get(f"/unsubscribe/{long_token}")
        assert resp.status_code == 410
        assert "Link expired" in resp.text


# ---------------------------------------------------------------------------
# POST — actual unsubscribe path
# ---------------------------------------------------------------------------


class TestPostEndpoint:
    def test_valid_token_succeeds(self, client):
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert "You have been unsubscribed" in resp.text

    def test_tampered_token_returns_410(self, client):
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        # Tamper one char near the start of the payload region.
        tampered = token[:30] + ("X" if token[30] != "X" else "Y") + token[31:]
        resp = client.post(f"/unsubscribe/{tampered}")
        assert resp.status_code == 410
        # Body is the generic "link expired" — never leaks which stage failed.
        assert "Link expired" in resp.text

    def test_expired_token_returns_410(self, client):
        from src.utils.unsubscribe_tokens import (
            DEFAULT_TTL_DAYS,
            mint,
        )
        old = int(time.time()) - DEFAULT_TTL_DAYS * 86_400 - 60
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET, issued_at=old)
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 410

    def test_unknown_tracking_id_still_returns_200(self, client):
        """Token verifies but tracking_id doesn't match a row in DB.

        From the recipient's perspective this is success — they've
        registered their intent; the absence of a matching row is an
        operator-side problem (campaign was wiped, etc.).
        """
        import main
        main.db = _build_mock_db(tracking_id_found=False)
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        assert "You have been unsubscribed" in resp.text

    def test_lead_deleted_lookup_still_returns_200(self, client):
        """tracking_id found, lead_unique_key set, but no matching lead row.

        The lead was wiped via /operator/account or manual cleanup; we
        have no email to suppress on. Still 200 — recipient already
        clicked, no further bookkeeping recoverable.
        """
        import main
        main.db = _build_mock_db(lead_email=None)
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 200

    def test_empty_token_returns_410(self, client):
        # FastAPI routes the literal `/unsubscribe/` as a different path
        # (no token captured) — 405 from router. Use a single space-like
        # token to hit the inner length check.
        resp = client.post("/unsubscribe/ ")
        # Space token is < 200 chars and >0, so we go through verify
        # which will raise InvalidToken — 410.
        assert resp.status_code == 410

    def test_no_api_key_required(self, client):
        """Public endpoint — recipient is not an authenticated LDS user."""
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        # No X-API-Key header.
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 200

    def test_writes_to_suppressions(self, client):
        """The success path must INSERT (identifier_type='email',
        identifier_value=victim@example.com, channel='all',
        reason='unsubscribe') into suppressions."""
        import main
        # Track every insert against suppressions.
        inserted: list[dict] = []
        db = MagicMock()

        def table_side_effect(name: str) -> MagicMock:
            chain = MagicMock()
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.in_.return_value = chain
            chain.limit.return_value = chain

            def insert(rows):
                inserted.extend(rows if isinstance(rows, list) else [rows])
                return chain

            chain.insert.side_effect = insert
            chain.upsert.return_value = chain
            if name == "campaign_messages":
                chain.execute.return_value = MagicMock(data=[{
                    "campaign_id": "camp-X",
                    "lead_unique_key": "lead-X",
                }])
            elif name == "leads":
                chain.execute.return_value = MagicMock(data=[{"email": "v@x.com"}])
            elif name == "suppressions":
                chain.execute.return_value = MagicMock(data=[{"id": 1}])
            else:
                chain.execute.return_value = MagicMock(data=[])
            return chain

        db.client.table.side_effect = table_side_effect
        main.db = db
        from src.utils.unsubscribe_tokens import mint
        token = mint(SAMPLE_TRACKING_ID, secret=SAMPLE_SECRET)
        resp = client.post(f"/unsubscribe/{token}")
        assert resp.status_code == 200
        # One suppression row inserted with the right shape.
        suppression_rows = [r for r in inserted if r.get("reason") == "unsubscribe"]
        assert len(suppression_rows) == 1
        row = suppression_rows[0]
        assert row["identifier_type"] == "email"
        assert row["identifier_value"] == "v@x.com"
        assert row["channel"] == "all"
        assert row["source_campaign_id"] == "camp-X"


if __name__ == "__main__":
    unittest.main()
