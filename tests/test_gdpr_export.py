"""GDPR data-export endpoint contract pins.

`GET /operator/data-export` is the operator's Article 20 (data
portability) + Article 15 (right of access) escape hatch. The endpoint
must:

1. **Return a ZIP** with the exact 4 members
   (``leads.csv``, ``campaigns.csv``, ``messages.csv``, ``audit_log.json``)
   regardless of whether the underlying tables are populated or empty.

2. **Round-trip cleanly**: every fixture row read from the mocked DB
   reappears unchanged in the CSV/JSON (modulo the CSV-injection guard
   prefix and the dict→JSON-string flatten for JSON cells).

3. **Auth-gate** via X-API-Key (every authed endpoint convention) and
   **rate-limit** to 1/day per rate-limit-key (proxy XFF when API key
   valid, peer IP otherwise).

4. **Apply the CSV-injection guard** — a lead with name ``=SUM(...)``
   appears in the CSV as ``'=SUM(...)``.

5. **Embed metadata in audit_log.json** — `schema_version`,
   `operator_email`, `row_counts`, ISO-Z `export_timestamp`,
   `orchestration_jobs` payload.

6. **Survive Supabase down** — returns 503 with no stack trace leak.

These tests are unit (no Supabase / Gemini / network). Pattern matches
`tests/test_error_message_leak.py` — mocked lazy singletons + slowapi
storage reset between tests.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


API_KEY = "test-gdpr-export-secret-key-32-chars-long"
HEADERS_OK = {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# Fixtures — 4 leads, 2 campaigns, 2 messages, 2 jobs.
# Each one exercises a specific round-trip concern.
# ---------------------------------------------------------------------------

_FIXTURE_LEADS = [
    {
        "unique_key": "lead-1",
        "name": "Alpha Tech",
        "website": "https://alpha.example.com",
        "email": "ops@alpha.example.com",
        "audit_status": "Completed",
        "seo_score": 85,
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-01T10:05:00Z",
    },
    {
        # CSV-injection canary — leading `=` must be prefixed with `'`.
        "unique_key": "lead-injection",
        "name": "=SUM(A1:A100)",
        "website": "https://attacker.example.com",
        "email": "evil@attacker.example.com",
        "audit_status": "Pending",
        "seo_score": None,
        "created_at": "2026-05-02T10:00:00Z",
        "updated_at": "2026-05-02T10:00:00Z",
    },
    {
        # Embedded CRLF in name — csv.QUOTE_MINIMAL wraps in quotes;
        # round-trip preserves the bytes intact.
        "unique_key": "lead-crlf",
        "name": "Brava\r\nLtd",
        "website": "https://brava.example.com",
        "email": "info@brava.example.com",
        "audit_status": "Completed",
        "seo_score": 70,
        "created_at": "2026-05-03T10:00:00Z",
        "updated_at": "2026-05-03T10:00:00Z",
    },
    {
        # JSONB cell — dict serialises to JSON string in CSV;
        # round-trip parses back to the same dict.
        "unique_key": "lead-jsonb",
        "name": "JsonCo",
        "website": "https://jsonco.example.com",
        "email": "info@jsonco.example.com",
        "audit_status": "Completed",
        "seo_score": 90,
        "audit_results": {
            "score": 85,
            "is_up": True,
            "tech_flags": {"wordpress": True, "cloudflare": False},
            "red_flags": ["no_ssl"],
        },
        "created_at": "2026-05-04T10:00:00Z",
        "updated_at": "2026-05-04T10:00:00Z",
    },
]

_FIXTURE_CAMPAIGNS = [
    {
        "id": "camp-1",
        "name": "Q2 Outreach",
        "channel": "email",
        "status": "draft",
        "segment_filter": "dentists",
        "created_at": "2026-04-01T10:00:00Z",
        "updated_at": "2026-04-01T10:00:00Z",
    },
    {
        "id": "camp-2",
        "name": "Q3 LinkedIn",
        "channel": "linkedin",
        "status": "active",
        "segment_filter": None,
        "created_at": "2026-04-15T10:00:00Z",
        "updated_at": "2026-04-15T10:00:00Z",
    },
]

_FIXTURE_MESSAGES = [
    {
        "id": "msg-1",
        "campaign_id": "camp-1",
        "lead_unique_key": "lead-1",
        "channel": "email",
        "subject": "Hello",
        "body": "Body.",
        "status": "pending",
        "created_at": "2026-04-02T10:00:00Z",
    },
    {
        "id": "msg-2",
        "campaign_id": "camp-1",
        "lead_unique_key": "lead-injection",
        "channel": "email",
        "subject": "Re: alpha",
        "body": "Body.",
        "status": "pending",
        "created_at": "2026-04-02T10:01:00Z",
    },
]

_FIXTURE_JOBS = [
    {
        "id": "job-1",
        "type": "discovery",
        "status": "completed",
        "filters": {"type": "pipeline"},
        "created_at": "2026-04-01T09:00:00Z",
        "updated_at": "2026-04-01T09:30:00Z",
    },
    {
        "id": "job-2",
        "type": "audit",
        "status": "running",
        "filters": None,
        "created_at": "2026-04-10T09:00:00Z",
        "updated_at": "2026-04-10T09:00:00Z",
    },
]


def _build_mock_db() -> MagicMock:
    """Mock SupabaseHelper.client so
    ``.table(name).select(...).order(...).execute().data`` returns the
    fixture for each table."""
    fixtures = {
        "leads": _FIXTURE_LEADS,
        "campaigns": _FIXTURE_CAMPAIGNS,
        "campaign_messages": _FIXTURE_MESSAGES,
        "orchestration_jobs": _FIXTURE_JOBS,
    }
    db = MagicMock()

    def table_side_effect(table_name):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.order.return_value = chain
        chain.execute.return_value = MagicMock(data=fixtures.get(table_name, []))
        return chain

    db.client.table.side_effect = table_side_effect
    return db


# ---------------------------------------------------------------------------
# Fixtures (pytest)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", API_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-gdpr-test")
    monkeypatch.setenv("OPERATOR_EMAIL", "operator@example.com")


@pytest.fixture(autouse=True)
def _reset_rate_limiter_and_db():
    """Clear slowapi storage AND restore the mock DB between tests.
    Mirrors the pattern from tests/test_error_message_leak.py."""
    import main

    try:
        main.limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:
        try:
            main.limiter.reset()
        except Exception:
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
# 1. ZIP structure
# ---------------------------------------------------------------------------


class TestExportStructure:
    def test_returns_zip_content_type(self, client):
        r = client.get("/operator/data-export", headers=HEADERS_OK)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "leadscraper-export-" in r.headers["content-disposition"]
        assert ".zip" in r.headers["content-disposition"]
        assert r.headers.get("cache-control") == "no-store"

    def test_zip_contains_4_expected_files(self, client):
        r = client.get("/operator/data-export", headers=HEADERS_OK)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert set(zf.namelist()) == {
            "leads.csv",
            "campaigns.csv",
            "messages.csv",
            "audit_log.json",
        }

    def test_zip_is_valid(self, client):
        r = client.get("/operator/data-export", headers=HEADERS_OK)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        bad = zf.testzip()  # None when archive is intact
        assert bad is None


# ---------------------------------------------------------------------------
# 2. Auth gate
# ---------------------------------------------------------------------------


class TestAuth:
    def test_no_api_key_returns_403(self, client):
        r = client.get("/operator/data-export")
        assert r.status_code == 403

    def test_wrong_api_key_returns_403(self, client):
        r = client.get("/operator/data-export", headers={"X-API-Key": "wrong-key"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 3. Rate limit (1/day)
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_second_call_within_day_returns_429(self, client):
        # Note: autouse fixture clears slowapi state BEFORE this test,
        # so the first call is fresh. Both calls happen WITHIN this
        # test function — the per-day bucket trips on the second one.
        r1 = client.get("/operator/data-export", headers=HEADERS_OK)
        assert r1.status_code == 200, f"first call should pass: {r1.text}"
        r2 = client.get("/operator/data-export", headers=HEADERS_OK)
        assert r2.status_code == 429, (
            f"second same-day call should 429, got {r2.status_code}: {r2.text}"
        )


# ---------------------------------------------------------------------------
# 4. Round-trip (the user's explicit ask: export → re-import → identical)
# ---------------------------------------------------------------------------


def _zip_from_export(client) -> zipfile.ZipFile:
    r = client.get("/operator/data-export", headers=HEADERS_OK)
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    text = zf.read(name).decode("utf-8")
    if not text.strip():
        return []
    return list(csv.DictReader(io.StringIO(text)))


class TestRoundTrip:
    """Export ZIP → parse back to dicts → assert deep-enough equality.

    "Identical" within the constraints CSV imposes:
      * All cell values come back as strings (CSV has no type system).
      * The CSV-injection guard prefixes ``=`` / ``@`` / ``+`` / ``-`` /
        ``\\t`` / ``\\r`` with ``'`` on write. This is intentional defense
        and tested separately in TestCsvInjection.
      * Dict / list values serialise as JSON strings; the operator
        round-trips them with ``json.loads`` on import.
    """

    def test_round_trip_leads_row_count_and_keys(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "leads.csv")
        assert len(rows) == len(_FIXTURE_LEADS)
        out_keys = [r["unique_key"] for r in rows]
        in_keys = [r["unique_key"] for r in _FIXTURE_LEADS]
        assert out_keys == in_keys

    def test_round_trip_lead_names(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "leads.csv")
        # First row clean.
        assert rows[0]["name"] == "Alpha Tech"
        # CRLF row round-trips intact via csv.QUOTE_MINIMAL.
        crlf_row = next(r for r in rows if r["unique_key"] == "lead-crlf")
        assert crlf_row["name"] == "Brava\r\nLtd"

    def test_round_trip_jsonb_cell(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "leads.csv")
        json_row = next(r for r in rows if r["unique_key"] == "lead-jsonb")
        parsed = json.loads(json_row["audit_results"])
        # Compare against the original fixture dict — bytes identical
        # after a JSON round-trip.
        original = next(l for l in _FIXTURE_LEADS if l["unique_key"] == "lead-jsonb")[
            "audit_results"
        ]
        assert parsed == original

    def test_round_trip_campaigns(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "campaigns.csv")
        assert len(rows) == len(_FIXTURE_CAMPAIGNS)
        for got, want in zip(rows, _FIXTURE_CAMPAIGNS):
            assert got["id"] == want["id"]
            assert got["name"] == want["name"]
            assert got["channel"] == want["channel"]
            assert got["status"] == want["status"]

    def test_round_trip_messages_preserves_fk_chain(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "messages.csv")
        assert len(rows) == len(_FIXTURE_MESSAGES)
        for got, want in zip(rows, _FIXTURE_MESSAGES):
            assert got["campaign_id"] == want["campaign_id"]
            assert got["lead_unique_key"] == want["lead_unique_key"]
            assert got["channel"] == want["channel"]

    def test_audit_log_metadata_shape(self, client):
        zf = _zip_from_export(client)
        payload = json.loads(zf.read("audit_log.json").decode("utf-8"))
        assert payload["schema_version"] == "1.0"
        assert payload["operator_email"] == "operator@example.com"
        assert payload["export_timestamp"].endswith("Z")
        # ISO-8601 with millisecond precision: ...T..:..:..\.\d{3}Z
        assert "T" in payload["export_timestamp"]
        # Row counts match the fixture.
        assert payload["row_counts"]["leads"] == len(_FIXTURE_LEADS)
        assert payload["row_counts"]["campaigns"] == len(_FIXTURE_CAMPAIGNS)
        assert payload["row_counts"]["campaign_messages"] == len(_FIXTURE_MESSAGES)
        assert payload["row_counts"]["orchestration_jobs"] == len(_FIXTURE_JOBS)
        # orchestration_jobs payload is the operator-action audit trail.
        assert len(payload["orchestration_jobs"]) == len(_FIXTURE_JOBS)
        assert payload["orchestration_jobs"][0]["id"] == "job-1"

    def test_audit_log_operator_email_null_when_unset(self, client, monkeypatch):
        """OPERATOR_EMAIL is the single-tenancy invariant env. When the
        operator runs in dev without setting it, the export should still
        succeed, with ``operator_email: null``."""
        monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
        zf = _zip_from_export(client)
        payload = json.loads(zf.read("audit_log.json").decode("utf-8"))
        assert payload["operator_email"] is None


# ---------------------------------------------------------------------------
# 5. CSV-injection guard
# ---------------------------------------------------------------------------


class TestCsvInjection:
    """Lead names / company names come from CSV uploads + Google Maps —
    both attacker-controllable. Every export site MUST apply
    ``sanitize_csv_cell`` (see ``src/utils/csv_helper.py``). The GDPR
    export is no exception even though the operator is exporting their
    OWN data — defense in depth so the exported file is also safe to
    forward to a third party (lawyer, regulator, downstream tool)
    without re-sanitising."""

    def test_leading_equals_prefixed_with_apostrophe(self, client):
        zf = _zip_from_export(client)
        text = zf.read("leads.csv").decode("utf-8")
        # csv.DictReader handles quoting; assert at the parsed-row level.
        rows = list(csv.DictReader(io.StringIO(text)))
        injection_row = next(r for r in rows if r["unique_key"] == "lead-injection")
        # Original: "=SUM(A1:A100)" → sanitized: "'=SUM(A1:A100)"
        assert injection_row["name"] == "'=SUM(A1:A100)", (
            f"CSV-injection guard didn't prefix `=` with `'`: {injection_row['name']!r}"
        )

    def test_legitimate_strings_pass_through(self, client):
        zf = _zip_from_export(client)
        rows = _read_csv(zf, "leads.csv")
        # "Alpha Tech" must round-trip unchanged — no spurious apostrophe.
        assert rows[0]["name"] == "Alpha Tech"


# ---------------------------------------------------------------------------
# 6. Empty tables (cold-start, fresh project)
# ---------------------------------------------------------------------------


class TestEmptyTables:
    def test_export_succeeds_with_all_empty(self, client):
        """Fresh deploy, no data yet. Export still produces 4 files."""
        import main

        empty_db = MagicMock()
        empty_chain = MagicMock()
        empty_chain.select.return_value = empty_chain
        empty_chain.order.return_value = empty_chain
        empty_chain.execute.return_value = MagicMock(data=[])
        empty_db.client.table.return_value = empty_chain
        main.db = empty_db

        r = client.get("/operator/data-export", headers=HEADERS_OK)
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert set(zf.namelist()) == {
            "leads.csv",
            "campaigns.csv",
            "messages.csv",
            "audit_log.json",
        }
        # Empty CSVs are zero-byte by design (no header without rows
        # since columns aren't known a priori).
        assert zf.read("leads.csv") == b""
        assert zf.read("campaigns.csv") == b""
        assert zf.read("messages.csv") == b""
        # audit_log.json still has metadata even when tables empty.
        payload = json.loads(zf.read("audit_log.json").decode("utf-8"))
        assert payload["row_counts"]["leads"] == 0
        assert payload["row_counts"]["campaigns"] == 0
        assert payload["row_counts"]["campaign_messages"] == 0
        assert payload["row_counts"]["orchestration_jobs"] == 0
        assert payload["orchestration_jobs"] == []


# ---------------------------------------------------------------------------
# 7. Supabase down
# ---------------------------------------------------------------------------


class TestDbUnavailable:
    def test_503_when_db_client_is_none(self, client):
        import main

        main.db = MagicMock()
        main.db.client = None

        r = client.get("/operator/data-export", headers=HEADERS_OK)
        assert r.status_code == 503
        # No stack-trace leak — the response is the canonical error_response shape.
        body = r.json()
        # Either {error: "..."} or {detail: "..."} — both acceptable.
        assert "error" in body or "detail" in body
        joined = json.dumps(body)
        assert "Traceback" not in joined
        assert "/Users/" not in joined  # no local path leak


if __name__ == "__main__":
    unittest.main()
