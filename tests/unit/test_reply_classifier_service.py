"""Unit tests for src/services/reply_classifier_service.py.

Pure-stub assertions — no Anthropic SDK, no DB writes. Pins:

- ``PHASE16_REPLY_CLASSIFIER=0`` (default) → every public method is a
  no-op + structured log.
- ``preprocess_reply_body`` strips quoted-text + sig + collapses
  blanks; never raises.
- ``body_hash`` is SHA256 hex of the *preprocessed* body so replay
  protection survives quote-trim variance.
- State-transition log envelopes carry the exact fields an operator
  would need to manually replay the auto-pause before the SQL lands.
- Disabled service skips classify + store + transitions entirely.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.reply_classifier_service import (
    OOO_RESUME_AFTER_DAYS,
    SUPPRESSION_SOURCE_WRONG_PERSON,
    TERMINAL_CLASSIFICATIONS,
    ReplyClassifierService,
    body_hash,
    is_enabled,
    preprocess_reply_body,
)


# --- is_enabled gate ------------------------------------------------------


class TestIsEnabled:
    def test_default_off(self) -> None:
        assert is_enabled(env={}) is False

    def test_explicit_zero(self) -> None:
        assert is_enabled(env={"PHASE16_REPLY_CLASSIFIER": "0"}) is False

    def test_explicit_one(self) -> None:
        assert is_enabled(env={"PHASE16_REPLY_CLASSIFIER": "1"}) is True

    def test_truthy_strings_not_accepted(self) -> None:
        # Only "1" enables — "true" / "yes" / "on" do NOT. Strict match
        # prevents accidental enable from a typo'd env var.
        for v in ("true", "yes", "on", "True", "TRUE", "enabled"):
            assert is_enabled(env={"PHASE16_REPLY_CLASSIFIER": v}) is False, v


# --- preprocess_reply_body ------------------------------------------------


class TestPreprocessReplyBody:
    def test_empty_returns_empty(self) -> None:
        assert preprocess_reply_body("") == ""

    def test_plain_text_passes_through(self) -> None:
        body = "Thanks, this looks great. Let's schedule a call."
        assert preprocess_reply_body(body) == body

    def test_quoted_lines_dropped(self) -> None:
        body = (
            "Sounds good, count me in.\n"
            "\n"
            "> On Mon, May 30, 2026 at 9:00 AM, Outreach <a@b.com> wrote:\n"
            "> Hi there, would you like a demo?\n"
            ">> Original message from system\n"
        )
        cleaned = preprocess_reply_body(body)
        # The new content survives; every quoted line + the "wrote:"
        # lead-in is dropped.
        assert cleaned == "Sounds good, count me in."

    def test_signature_delimiter_drops_below(self) -> None:
        body = (
            "Interested. Let's set something up next week.\n"
            "\n"
            "-- \n"
            "Sarah Smith\n"
            "VP Marketing | Acme Corp\n"
            "555-1234\n"
        )
        cleaned = preprocess_reply_body(body)
        assert cleaned == "Interested. Let's set something up next week."

    def test_gmail_wrote_pattern(self) -> None:
        body = (
            "Please remove me from your list.\n"
            "On Mon, May 30, 2026 at 9:00 AM John wrote:\n"
            "Hi, would you like to meet?"
        )
        cleaned = preprocess_reply_body(body)
        assert cleaned == "Please remove me from your list."

    def test_croatian_wrote_pattern(self) -> None:
        body = (
            "Hvala, ne treba nam.\n"
            "Dana 30.05.2026. Ivan Horvat napisao je:\n"
            "Pozdrav, jeste li zainteresirani?"
        )
        cleaned = preprocess_reply_body(body)
        assert cleaned == "Hvala, ne treba nam."

    def test_only_quoted_returns_empty(self) -> None:
        body = "> > Old message\n> > More old"
        assert preprocess_reply_body(body) == ""


# --- body_hash ------------------------------------------------------------


class TestBodyHash:
    def test_sha256_hex_shape(self) -> None:
        h = body_hash("hello world")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        assert body_hash("abc") == body_hash("abc")

    def test_distinct_inputs_distinct_hashes(self) -> None:
        assert body_hash("a") != body_hash("b")


# --- ReplyClassifierService gating ----------------------------------------


class TestServiceDisabled:
    @pytest.fixture
    def svc(self) -> ReplyClassifierService:
        return ReplyClassifierService(db=MagicMock(), enabled=False)

    @pytest.mark.asyncio
    async def test_handle_replied_event_returns_none_when_disabled(
        self, svc: ReplyClassifierService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        result = await svc.handle_replied_event(
            reply_body="Sounds great, let's chat.",
            lead_unique_key="acme.com_alice@acme.com",
            campaign_message_id="msg-123",
            provider_event_id="svix-evt-1",
        )
        assert result is None
        msgs = [r.message for r in caplog.records]
        assert any("classifier disabled" in m for m in msgs), msgs

    @pytest.mark.asyncio
    async def test_disabled_service_does_not_call_anthropic(
        self, svc: ReplyClassifierService,
    ) -> None:
        # Mock the would-be SDK entry point — if disabled gating works,
        # this is never hit.
        svc.classify_via_anthropic = AsyncMock(side_effect=AssertionError("should not be called"))
        svc.store_classification = AsyncMock(side_effect=AssertionError("should not be called"))
        svc.apply_state_transitions = AsyncMock(side_effect=AssertionError("should not be called"))
        result = await svc.handle_replied_event(
            reply_body="anything",
            lead_unique_key="x",
            campaign_message_id=None,
            provider_event_id="svix-evt-2",
        )
        assert result is None
        # AsyncMocks NOT called.
        svc.classify_via_anthropic.assert_not_called()
        svc.store_classification.assert_not_called()
        svc.apply_state_transitions.assert_not_called()


# --- ReplyClassifierService enabled (still STUB) --------------------------


class TestServiceEnabledStub:
    """Enabled-flag path runs preprocess + body_hash + delegates to the
    classify_via_anthropic stub which returns None — pipeline aborts
    cleanly (no store, no transitions). Real Anthropic wiring lands in
    a follow-up PR.
    """

    @pytest.mark.asyncio
    async def test_classify_stub_returns_none_aborts_pipeline(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        svc = ReplyClassifierService(db=MagicMock(), enabled=True)
        svc.store_classification = AsyncMock(side_effect=AssertionError("not yet"))
        svc.apply_state_transitions = AsyncMock(side_effect=AssertionError("not yet"))
        result = await svc.handle_replied_event(
            reply_body="Sounds great.",
            lead_unique_key="acme.com_alice@acme.com",
            campaign_message_id="msg-1",
            provider_event_id="svix-evt-3",
        )
        # Stub returns None -> handle_replied_event short-circuits.
        assert result is None
        svc.store_classification.assert_not_called()
        svc.apply_state_transitions.assert_not_called()
        # And the warn line tells the operator the stub fired.
        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("classify_via_anthropic stub" in m for m in warn_msgs), warn_msgs

    @pytest.mark.asyncio
    async def test_empty_preprocessed_body_skips(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        svc = ReplyClassifierService(db=MagicMock(), enabled=True)
        svc.classify_via_anthropic = AsyncMock(side_effect=AssertionError("not on empty"))
        result = await svc.handle_replied_event(
            reply_body="> > all quoted",  # preprocess returns ""
            lead_unique_key="x",
            campaign_message_id=None,
            provider_event_id="svix-evt-4",
        )
        assert result is None
        svc.classify_via_anthropic.assert_not_called()
        msgs = [r.message for r in caplog.records]
        assert any("empty body after preprocess" in m for m in msgs), msgs


# --- State-transition log envelopes ---------------------------------------


class TestStateTransitionLogs:
    """The stub's job is to log the would-be mutation in a shape the
    operator can grep + replay. Each category's log envelope is pinned
    so a future refactor that drops one of these fields breaks the
    test loudly.
    """

    @pytest.fixture
    def svc(self) -> ReplyClassifierService:
        return ReplyClassifierService(db=MagicMock(), enabled=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal", sorted(TERMINAL_CLASSIFICATIONS))
    async def test_terminal_logs_pause(
        self,
        svc: ReplyClassifierService,
        caplog: pytest.LogCaptureFixture,
        terminal: str,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        await svc.apply_state_transitions(
            classification=terminal,
            confidence=0.91,
            lead_unique_key="acme.com_bob@acme.com",
        )
        # First log line = pause intent.
        rec = next(r for r in caplog.records if "paused_by_reply" in r.message)
        # The extras dict carries the operator-replay fields.
        assert getattr(rec, "lead_unique_key", None) == "acme.com_bob@acme.com"
        assert getattr(rec, "classification", None) == terminal
        assert getattr(rec, "confidence", None) == 0.91
        assert getattr(rec, "target_status", None) == "paused_by_reply"
        assert getattr(rec, "from_statuses", None) == ["pending", "dispatching"]

    @pytest.mark.asyncio
    async def test_unsubscribe_also_logs_suppression(
        self, svc: ReplyClassifierService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        await svc.apply_state_transitions(
            classification="unsubscribe_request",
            confidence=0.99,
            lead_unique_key="x",
        )
        # Two log lines — pause + suppression intent.
        msgs = [r.message for r in caplog.records]
        assert any("paused_by_reply" in m for m in msgs)
        assert any("INSERT suppression" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_wrong_person_logs_suppression(
        self, svc: ReplyClassifierService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        await svc.apply_state_transitions(
            classification="wrong_person",
            confidence=0.88,
            lead_unique_key="x",
        )
        rec = next(r for r in caplog.records if "wrong_person" in r.message)
        assert getattr(rec, "source", None) == SUPPRESSION_SOURCE_WRONG_PERSON

    @pytest.mark.asyncio
    async def test_ooo_logs_resume_timestamp(
        self, svc: ReplyClassifierService, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="src.services.reply_classifier_service")
        await svc.apply_state_transitions(
            classification="ooo",
            confidence=0.85,
            lead_unique_key="x",
        )
        rec = next(r for r in caplog.records if "defer" in r.message)
        # expected_resume_at is the operator-grep-able receipt.
        resume_iso = getattr(rec, "expected_resume_at", None)
        assert isinstance(resume_iso, str)
        # ISO 8601 shape with timezone.
        assert "T" in resume_iso
        assert resume_iso.endswith("+00:00") or resume_iso.endswith("Z")
        assert f"{OOO_RESUME_AFTER_DAYS}d" in rec.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "non_terminal", ["asking_for_info", "bounce_soft", "bounce_hard", "auto_reply", "other"]
    )
    async def test_non_terminal_no_state_log(
        self,
        svc: ReplyClassifierService,
        caplog: pytest.LogCaptureFixture,
        non_terminal: str,
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="src.services.reply_classifier_service")
        await svc.apply_state_transitions(
            classification=non_terminal,
            confidence=0.7,
            lead_unique_key="x",
        )
        # Should NOT contain pause/suppression/defer language.
        msgs = [r.message for r in caplog.records]
        assert not any("paused_by_reply" in m for m in msgs)
        assert not any("INSERT suppression" in m for m in msgs)
        assert not any("defer" in m for m in msgs)
