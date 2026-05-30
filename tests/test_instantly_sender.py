"""Tests for InstantlyDispatcher + InstantlyLeadPayload.

Unit tier (no marker) covers: pydantic model mapping, suppression
precheck against a mocked Supabase client, batch boundary slicing,
dry-run behaviour, response-shape parsing for 200/400/401/429.

Live tier (`@pytest.mark.live`) requires real INSTANTLY_API_KEY +
sandbox campaign ID — exercises the actual v2 API. Skipped by default.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import pytest

from src.integrations.instantly_models import (
    InstantlyError,
    InstantlyLeadPayload,
    InstantlyPushResult,
)
from src.integrations.instantly_sender import (
    DEFAULT_BATCH_SIZE,
    INSTANTLY_BULK_HARD_LIMIT,
    InstantlyDispatcher,
)


# ---------------------------------------------------------------------------
# Pydantic mapping
# ---------------------------------------------------------------------------


class TestInstantlyLeadPayload:
    def test_from_lds_lead_maps_canonical_columns(self):
        lead = {
            "email": "a@example.com",
            "first_name": "Ana",
            "last_name": "Anić",
            "company_name": "Acme d.o.o.",
            "website": "https://acme.example",
            "unique_key": "u-001",
            "outreach_score": 73,
            "lead_source": "google_maps",
        }
        p = InstantlyLeadPayload.from_lds_lead(
            lead,
            personalization="Quick note about Acme.",
            dispatched_at="2026-05-25T13:00:00+00:00",
        )
        assert p.email == "a@example.com"
        assert p.first_name == "Ana"
        assert p.company_name == "Acme d.o.o."
        assert p.custom_variables["lds_lead_id"] == "u-001"
        assert p.custom_variables["lds_audit_score"] == 73
        assert p.custom_variables["lds_discovery_source"] == "google_maps"
        assert p.custom_variables["lds_dispatched_at"] == "2026-05-25T13:00:00+00:00"

    def test_from_lds_lead_nulls_missing_optional_fields(self):
        p = InstantlyLeadPayload.from_lds_lead({"email": "b@example.com"})
        assert p.first_name is None
        assert p.company_name is None
        assert p.website is None
        assert p.personalization is None

    def test_extra_forbid_rejects_unknown_field(self):
        with pytest.raises(ValueError):
            InstantlyLeadPayload(email="x@x.com", bogus_field="oops")  # type: ignore[call-arg]

    def test_invalid_email_rejected(self):
        with pytest.raises(ValueError):
            InstantlyLeadPayload(email="not-an-email")

    def test_lds_keys_pinned(self):
        # Phase 14.2 PR β: list_unsubscribe + list_unsubscribe_post for
        # RFC 8058 List-Unsubscribe-Post header (custom-vars-to-header
        # bridge on Instantly's side).
        # Phase 14.3: lds_message_id (campaign_messages.id) so the
        # email_sent webhook handler can do a targeted first-hit-wins
        # UPDATE.
        assert InstantlyLeadPayload.LDS_KEYS == frozenset(
            {
                "lds_lead_id",
                "lds_message_id",
                "lds_audit_score",
                "lds_discovery_source",
                "lds_dispatched_at",
                "list_unsubscribe",
                "list_unsubscribe_post",
            }
        )

    def test_from_lds_lead_threads_list_unsubscribe(self):
        # Caller passes the angle-bracketed value Instantly expects per
        # the custom-vars-to-header bridge convention. from_lds_lead must
        # set both list_unsubscribe AND list_unsubscribe_post when the
        # kwarg is provided — Gmail/Yahoo 2024 one-click compliance
        # requires both headers.
        p = InstantlyLeadPayload.from_lds_lead(
            {"email": "c@example.com", "unique_key": "u-002"},
            list_unsubscribe="<https://leaddatascraper.example/unsubscribe/abc>",
        )
        assert p.custom_variables["list_unsubscribe"] == (
            "<https://leaddatascraper.example/unsubscribe/abc>"
        )
        assert p.custom_variables["list_unsubscribe_post"] == (
            "List-Unsubscribe=One-Click"
        )

    def test_from_lds_lead_omits_list_unsubscribe_when_none(self):
        p = InstantlyLeadPayload.from_lds_lead({"email": "d@example.com"})
        assert "list_unsubscribe" not in p.custom_variables
        assert "list_unsubscribe_post" not in p.custom_variables


class TestInstantlyPushResult:
    def test_total_attempted_property(self):
        r = InstantlyPushResult(success_count=5, skipped_suppressed=2, failed_count=1)
        assert r.total_attempted == 8

    def test_extra_forbid(self):
        with pytest.raises(ValueError):
            InstantlyPushResult(
                success_count=1, skipped_suppressed=0, failed_count=0, junk="x"
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class _FakeQuery:
    """Minimal supabase-py query stub: .table().select().eq().in_().execute().

    ``.eq()`` was added in Phase 14.2 to support the suppressions
    table's (identifier_type='email', channel IN [...]) predicate; the
    captured eq/in_ filters surface in ``captured`` so tests can assert
    the precheck SQL shape.
    """

    def __init__(self, data: list[dict[str, Any]]):
        self._data = data
        self.captured: dict[str, Any] = {}

    def select(self, _cols: str) -> "_FakeQuery":
        return self

    def eq(self, col: str, value: Any) -> "_FakeQuery":
        self.captured.setdefault("eq", {})[col] = value
        return self

    def in_(self, col: str, values: list[Any]) -> "_FakeQuery":
        self.captured.setdefault("in_", {})[col] = list(values)
        # Back-compat: keep the old single-filter capture for existing
        # ledger-side assertions that look at .captured['col'] / ['values'].
        self.captured["col"] = col
        self.captured["values"] = list(values)
        return self

    def insert(self, rows: list[dict[str, Any]]) -> "_FakeQuery":
        self.captured["inserted"] = list(rows)
        return self

    def execute(self) -> "_FakeQuery":
        return self

    @property
    def data(self) -> list[dict[str, Any]]:
        return self._data


class _FakeDB:
    def __init__(self, suppressed: Optional[list[str]] = None):
        self.suppressed = suppressed or []
        self.last_query: Optional[_FakeQuery] = None
        self.inserted_ledger: list[dict[str, Any]] = []

    def table(self, name: str) -> _FakeQuery:
        if name == "suppressions":
            # Phase 14.2 renamed email_suppression → suppressions; email
            # rows surface under identifier_value.
            q = _FakeQuery([{"identifier_value": e} for e in self.suppressed])
        elif name == "email_send_ledger":
            q = _FakeQuery([])
            # Capture ledger inserts.
            orig_insert = q.insert

            def insert(rows: list[dict[str, Any]]) -> _FakeQuery:  # type: ignore[no-redef]
                self.inserted_ledger.extend(rows)
                return orig_insert(rows)

            q.insert = insert  # type: ignore[assignment]
        else:
            q = _FakeQuery([])
        self.last_query = q
        return q


class TestDispatcherConstruction:
    def test_aup_invariant_dispatch_type_cold(self):
        d = InstantlyDispatcher(api_key="k", default_campaign_id="c")
        assert d.DISPATCH_TYPE == "cold"
        assert d.PROVIDER_NAME == "instantly"
        assert d.SUPPORTS_WEBHOOKS is True
        assert d.SUPPORTS_IDEMPOTENCY is True

    def test_invalid_batch_size_rejected(self):
        with pytest.raises(ValueError):
            InstantlyDispatcher(api_key="k", default_campaign_id="c", batch_size=0)
        with pytest.raises(ValueError):
            InstantlyDispatcher(
                api_key="k",
                default_campaign_id="c",
                batch_size=INSTANTLY_BULK_HARD_LIMIT + 1,
            )

    def test_default_batch_size(self):
        d = InstantlyDispatcher(api_key="k", default_campaign_id="c")
        assert d.batch_size == DEFAULT_BATCH_SIZE


class TestSendPath:
    @pytest.mark.asyncio
    async def test_send_raises_not_implemented(self):
        d = InstantlyDispatcher(api_key="k", default_campaign_id="c")
        with pytest.raises(NotImplementedError, match="InstantlyDispatcher.send\\(\\) is not supported; use push_leads\\(\\) instead."):
            await d.send(to="test@example.com", subject="Test", body="Test")

class TestPushLeadsErrorPaths:
    @pytest.mark.asyncio
    async def test_no_campaign_id_raises(self):
        d = InstantlyDispatcher(api_key="k", default_campaign_id=None)
        with pytest.raises(ValueError, match="campaign_id required"):
            await d.push_leads([{"email": "a@b.com"}])

    @pytest.mark.asyncio
    async def test_no_api_key_raises(self):
        d = InstantlyDispatcher(api_key="", default_campaign_id="c")
        with pytest.raises(ValueError, match="INSTANTLY_API_KEY not configured"):
            await d.push_leads([{"email": "a@b.com"}])

    @pytest.mark.asyncio
    async def test_empty_lead_list_returns_zero_result(self):
        d = InstantlyDispatcher(api_key="k", default_campaign_id="c")
        result = await d.push_leads([])
        assert result.success_count == 0
        assert result.skipped_suppressed == 0
        assert result.failed_count == 0


class TestSuppressionPrecheck:
    @pytest.mark.asyncio
    async def test_suppressed_emails_skipped_in_dry_run(self):
        db = _FakeDB(suppressed=["sup@example.com"])
        d = InstantlyDispatcher(
            api_key="k", default_campaign_id="c", dry_run=True, db=db
        )
        leads = [
            {"email": "ok@example.com", "unique_key": "u1"},
            {"email": "sup@example.com", "unique_key": "u2"},
        ]
        result = await d.push_leads(leads)
        assert result.dry_run is True
        assert result.skipped_suppressed == 1
        assert result.success_count == 1  # only ok@example.com
        assert db.inserted_ledger == []  # dry-run skips ledger writes

    @pytest.mark.asyncio
    async def test_suppression_precheck_batch_query_single_round_trip(self):
        db = _FakeDB(suppressed=[])
        d = InstantlyDispatcher(
            api_key="k", default_campaign_id="c", dry_run=True, db=db
        )
        leads = [
            {"email": f"u{i}@example.com", "unique_key": f"u{i}"} for i in range(50)
        ]
        await d.push_leads(leads)
        assert db.last_query is not None
        assert len(db.last_query.captured["values"]) == 50


class TestDryRunBehavior:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_touch_api_or_ledger(self):
        db = _FakeDB()
        d = InstantlyDispatcher(
            api_key="k", default_campaign_id="c", dry_run=True, db=db
        )
        leads = [
            {"email": "a@x.com", "unique_key": "u1"},
            {"email": "b@x.com", "unique_key": "u2"},
        ]
        result = await d.push_leads(leads)
        assert result.dry_run is True
        assert result.success_count == 2
        assert result.failed_count == 0
        assert db.inserted_ledger == []


class TestListUnsubscribeThreading:
    """push_leads wraps each bare URL in ``<>`` and threads via
    ``custom_variables.list_unsubscribe`` per Instantly's bridge.

    Closes the Phase 15.2 wiring drift surfaced by /security-audit:run.
    """

    @pytest.mark.asyncio
    async def test_push_leads_wraps_and_threads_unsubscribe(
        self,
        monkeypatch,
    ):
        captured: list[dict[str, Any]] = []
        original_from_lds_lead = InstantlyLeadPayload.from_lds_lead

        def _spy(cls, lead, **kwargs):
            captured.append({"uk": lead.get("unique_key"), **kwargs})
            return original_from_lds_lead(lead, **kwargs)

        monkeypatch.setattr(
            InstantlyLeadPayload,
            "from_lds_lead",
            classmethod(_spy),
        )

        db = _FakeDB()
        d = InstantlyDispatcher(
            api_key="k", default_campaign_id="c", dry_run=True, db=db
        )
        leads = [
            {"email": "a@x.com", "unique_key": "u1"},
            {"email": "b@x.com", "unique_key": "u2"},
        ]
        await d.push_leads(
            leads,
            list_unsubscribe_urls={
                "u1": "https://leaddatascraper.example/unsubscribe/tok1",
                # u2 absent — must not appear in custom_vars
            },
        )

        by_uk = {c["uk"]: c for c in captured}
        assert by_uk["u1"]["list_unsubscribe"] == (
            "<https://leaddatascraper.example/unsubscribe/tok1>"
        )
        assert by_uk["u2"]["list_unsubscribe"] is None

    @pytest.mark.asyncio
    async def test_push_leads_no_unsub_kwarg_keeps_existing_behavior(
        self,
        monkeypatch,
    ):
        captured: list[dict[str, Any]] = []
        original = InstantlyLeadPayload.from_lds_lead

        def _spy(cls, lead, **kwargs):
            captured.append(kwargs)
            return original(lead, **kwargs)

        monkeypatch.setattr(
            InstantlyLeadPayload,
            "from_lds_lead",
            classmethod(_spy),
        )

        d = InstantlyDispatcher(
            api_key="k", default_campaign_id="c", dry_run=True, db=_FakeDB()
        )
        await d.push_leads([{"email": "a@x.com", "unique_key": "u1"}])
        # Backwards compat: callers that omit list_unsubscribe_urls see
        # the same behaviour as before — no list_unsubscribe in vars.
        assert captured[0]["list_unsubscribe"] is None


# ---------------------------------------------------------------------------
# Live tier — opt-in
# ---------------------------------------------------------------------------


live = pytest.mark.skipif(
    not os.environ.get("INSTANTLY_API_KEY")
    or not os.environ.get("INSTANTLY_DEFAULT_CAMPAIGN_ID"),
    reason="Set INSTANTLY_API_KEY + INSTANTLY_DEFAULT_CAMPAIGN_ID for live tests",
)


@pytest.mark.live
@live
class TestLiveSandbox:
    @pytest.mark.asyncio
    async def test_single_lead_push_to_sandbox(self):
        d = InstantlyDispatcher()
        try:
            result = await d.push_leads(
                [
                    {"email": "smoke-test@invalid.example", "unique_key": "smoke-1"},
                ]
            )
            # Sandbox accepts the row (probably as 200 with success=1) OR
            # rejects with a domain-specific error code; either way the
            # result shape is what we contract for.
            assert isinstance(result, InstantlyPushResult)
            assert result.total_attempted >= 1
        finally:
            await d.aclose()
