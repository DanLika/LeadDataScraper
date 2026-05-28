"""Sequence engine smoke — Phase 14+15 dispatcher invariants pinned.

Two tiers:

* **Offline** (default `pytest` run): template_renderer text/html autoescape +
  ALLOWED_VARS allowlist, send_window for Europe/Sarajevo Mon-Fri 09:00-17:00,
  variant_selector weighted distribution + deterministic-seed env gate,
  sequence_advancer branch gating + window-bump + thread_with_prior wiring.
  No DB; no external service.

* **Live** (``-m live``): SQL CHECK constraint probes against prod (or branch)
  Postgres via psycopg. Each negative case INSERTed inside a ``BEGIN ; ...
  ROLLBACK`` block so no row persists. Skipped when ``SUPABASE_DATABASE_URL``
  is absent or ``psycopg`` is not installed.

Pins:
  * 11 CHECKs from ``supabase_schema.sql`` (commit 8f5d8f0).
  * ``template_renderer.render`` content_type routing.
  * ``send_window.is_within_window`` boundary semantics.
  * ``variant_selector.select_variant`` weighted distribution + ``VARIANT_SELECTOR_ALLOW_SEED`` gate.
  * ``sequence_advancer.advance_to_next_step`` schedule-on-advance design.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from src.services.sequence_advancer import advance_to_next_step
from src.services.template_renderer import (
    ALLOWED_VARS,
    MissingVariableError,
    render,
)
from src.services.variant_selector import select_variant
from src.utils.send_window import is_within_window


# ---------------------------------------------------------------------------
# Offline tier — template_renderer
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestTemplateRendererContentType:
    """``content_type`` routes between Jinja2 autoescape ON (html) and OFF (text)."""

    def test_text_mode_does_not_escape_lead_data(self):
        xss = "<script>alert(1)</script>"
        body = render(
            "Hi from {{ company }}. {{ unsubscribe_url }}",
            {"company": xss, "unsubscribe_url": "https://x/u"},
            content_type="text",
        )
        assert "<script>alert(1)</script>" in body

    def test_html_mode_escapes_attacker_controlled_company(self):
        body = render(
            "Hi from {{ company }}. {{ unsubscribe_url }}",
            {"company": "<script>alert(1)</script>", "unsubscribe_url": "https://x/u"},
            content_type="html",
        )
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        assert "<script>" not in body

    def test_html_mode_escapes_ampersand(self):
        assert (
            render("{{ company }}", {"company": "X&Y"}, content_type="html")
            == "X&amp;Y"
        )

    def test_text_mode_preserves_ampersand(self):
        assert render("{{ company }}", {"company": "X&Y"}, content_type="text") == "X&Y"


@pytest.mark.smoke
class TestTemplateRendererAllowlist:
    """``ALLOWED_VARS`` filters context BEFORE bind; bogus vars raise."""

    def test_business_name_not_in_allowlist(self):
        # User-spec drift: the wire format uses `company`, never `business_name`.
        assert "business_name" not in ALLOWED_VARS
        assert "company" in ALLOWED_VARS

    def test_unallowed_var_raises_missing_variable(self):
        with pytest.raises(MissingVariableError):
            render("Hello {{ business_name }}", {"business_name": "Acme"})

    def test_unallowed_var_in_context_is_silently_dropped(self):
        # Allowlist filter happens BEFORE render — extra ctx keys ignored,
        # not raised; template that only references allowed vars renders fine.
        body = render(
            "{{ first_name }}",
            {"first_name": "Ada", "secret_field": "leaked"},
            content_type="text",
        )
        assert body == "Ada"


# ---------------------------------------------------------------------------
# Offline tier — send_window
# ---------------------------------------------------------------------------


_WINDOW_KW = dict(
    step_send_window_start="09:00",
    step_send_window_end="17:00",
    step_send_days="mon,tue,wed,thu,fri",
    timezone_name="Europe/Sarajevo",
)


# Europe/Sarajevo in May/June = CEST (UTC+2); local 14:00 = 12:00 UTC.
_WINDOW_CASES = [
    # (now_utc, expected_in_window, label)
    (datetime(2026, 5, 30, 11, 0, tzinfo=timezone.utc), False, "Saturday 13:00 local"),
    (datetime(2026, 5, 31, 11, 0, tzinfo=timezone.utc), False, "Sunday 13:00 local"),
    (datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc), False, "Tuesday 03:00 local"),
    (datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc), True, "Tuesday 14:00 local"),
    (
        datetime(2026, 6, 2, 7, 0, tzinfo=timezone.utc),
        True,
        "Tuesday 09:00 local (start inclusive)",
    ),
    (
        datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc),
        False,
        "Tuesday 17:00 local (end exclusive)",
    ),
    (datetime(2026, 6, 2, 14, 59, tzinfo=timezone.utc), True, "Tuesday 16:59 local"),
]


@pytest.mark.smoke
@pytest.mark.parametrize("now_utc,expected,label", _WINDOW_CASES)
def test_send_window_boundaries(now_utc, expected, label):
    result = is_within_window(now_utc=now_utc, **_WINDOW_KW)
    assert result.in_window is expected, label


@pytest.mark.smoke
def test_send_window_returns_next_open_when_outside():
    # Saturday → next open is Monday 09:00 Sarajevo = 07:00 UTC.
    sat = datetime(2026, 5, 30, 11, 0, tzinfo=timezone.utc)
    result = is_within_window(now_utc=sat, **_WINDOW_KW)
    assert result.next_window_start_utc == datetime(
        2026, 6, 1, 7, 0, tzinfo=timezone.utc
    )


@pytest.mark.smoke
def test_send_window_unknown_day_tokens_silently_dropped():
    # 'foo' is junk; resolver keeps the rest. Tuesday 14:00 local still in window.
    result = is_within_window(
        step_send_window_start="09:00",
        step_send_window_end="17:00",
        step_send_days="mon,foo,tue",
        timezone_name="Europe/Sarajevo",
        now_utc=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
    )
    assert result.in_window is True


# ---------------------------------------------------------------------------
# Offline tier — variant_selector
# ---------------------------------------------------------------------------


@dataclass
class _V:
    id: str
    variant_label: str
    weight: int


@pytest.mark.smoke
class TestVariantSelector:
    def test_empty_iterable_returns_none(self):
        assert select_variant([]) is None

    def test_single_variant_returns_that_variant(self):
        only = _V(id="x", variant_label="A", weight=99)
        assert select_variant([only]) is only

    def test_even_weights_split_roughly_equal(self):
        a = _V(id="a", variant_label="A", weight=50)
        b = _V(id="b", variant_label="B", weight=50)
        n = 5000
        counts = Counter(select_variant([a, b]).id for _ in range(n))
        # Allow generous slack — Chebyshev bound for fair coin over 5k is wide.
        assert abs(counts["a"] - counts["b"]) < n * 0.10

    def test_skewed_weights_produce_skewed_distribution(self):
        a = _V(id="a", variant_label="A", weight=25)
        b = _V(id="b", variant_label="B", weight=75)
        n = 5000
        counts = Counter(select_variant([a, b]).id for _ in range(n))
        ratio = counts["b"] / max(1, counts["a"])
        assert 2.5 < ratio < 3.5  # expect ~3.0

    def test_deterministic_seed_honored_under_env_gate(self, monkeypatch):
        monkeypatch.setenv("VARIANT_SELECTOR_ALLOW_SEED", "1")
        a = _V(id="a", variant_label="A", weight=50)
        b = _V(id="b", variant_label="B", weight=50)
        picks = {
            select_variant([a, b], deterministic_seed="lead-42").id for _ in range(10)
        }
        assert len(picks) == 1  # all identical

    def test_deterministic_seed_ignored_without_env_gate(self, monkeypatch):
        monkeypatch.delenv("VARIANT_SELECTOR_ALLOW_SEED", raising=False)
        a = _V(id="a", variant_label="A", weight=50)
        b = _V(id="b", variant_label="B", weight=50)
        # Without gate, falls back to SystemRandom → both ids show up in 1000 calls.
        ids = {
            select_variant([a, b], deterministic_seed="lead-42").id for _ in range(1000)
        }
        assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# Offline tier — sequence_advancer
# ---------------------------------------------------------------------------


@dataclass
class _FakeStep:
    id: str
    step_index: int
    channel: str = "email"
    delay_days: int = 0
    delay_hours: int = 0
    branch_condition: str = "always"
    send_window_start: str = "09:00"
    send_window_end: str = "17:00"
    send_days: str = "mon,tue,wed,thu,fri"
    thread_with_prior: bool = False


class _FakeStepRepo:
    def __init__(self, steps):
        self._by_id = {s.id: s for s in steps}
        self._steps = steps

    async def get_by_id(self, sid):
        return self._by_id.get(sid)

    async def get_by_index(self, seq_id, idx):
        for s in self._steps:
            if s.step_index == idx:
                return s
        return None


class _FakeMsgRepo:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_next_step_row(self, **kw):
        self.inserted.append(kw)
        return {"id": f"msg-{len(self.inserted)}"}


def _base_message():
    return {
        "id": "m1",
        "lead_unique_key": "lead-1",
        "campaign_id": "camp-1",
        "sequence_id": "seq-1",
        "step_id": "s1",
        "provider_message_id": "prov-1",
    }


@pytest.fixture(autouse=True)
def _sarajevo_tz(monkeypatch):
    monkeypatch.setenv("SEND_WINDOW_DEFAULT_TZ", "Europe/Sarajevo")


@pytest.mark.smoke
class TestSequenceAdvancer:
    """schedule-on-advance: _sent advances UNLESS next-step is reply-only;
    _replied advances ONLY IF next-step IS reply-only."""

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def test_sent_event_advances_always_branch(self):
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2, branch_condition="always")
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
            )
        )
        assert res.advanced is True
        assert res.next_step_id == "s2"

    def test_sent_event_skips_reply_only_branch(self):
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2, branch_condition="replied")
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
            )
        )
        assert res.advanced is False
        assert res.reason == "next_step_replied_only"

    def test_replied_event_skips_always_branch(self):
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2, branch_condition="always")
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="replied",
            )
        )
        assert res.advanced is False
        assert res.reason == "next_step_not_replied_branch"

    def test_replied_event_advances_into_replied_branch(self):
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2, branch_condition="replied")
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="replied",
            )
        )
        assert res.advanced is True

    def test_sequence_complete_returns_reason(self):
        s1 = _FakeStep(id="s1", step_index=1)
        repo, msgs = _FakeStepRepo([s1]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
            )
        )
        assert res.advanced is False
        assert res.reason == "sequence_complete"

    def test_missing_context_returns_reason(self):
        bad = _base_message()
        bad.pop("sequence_id")
        s1 = _FakeStep(id="s1", step_index=1)
        repo, msgs = _FakeStepRepo([s1]), _FakeMsgRepo()
        res = self._run(
            advance_to_next_step(
                current_message=bad,
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
            )
        )
        assert res.reason == "missing_sequence_context"

    def test_out_of_window_send_bumps_scheduled_at(self):
        # Saturday _sent → next-step schedule bumped to Monday 09:00 Sarajevo (07:00 UTC).
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2)
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        sat = datetime(2026, 5, 30, 11, 0, tzinfo=timezone.utc)
        res = self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
                sent_at=sat,
            )
        )
        assert res.scheduled_at == "2026-06-01T07:00:00+00:00"

    def test_thread_with_prior_wires_in_reply_to(self):
        s1 = _FakeStep(id="s1", step_index=1)
        s2 = _FakeStep(id="s2", step_index=2, thread_with_prior=True)
        repo, msgs = _FakeStepRepo([s1, s2]), _FakeMsgRepo()
        self._run(
            advance_to_next_step(
                current_message=_base_message(),
                step_repo=repo,
                message_repo=msgs,
                event_type="sent",
            )
        )
        assert msgs.inserted[0]["in_reply_to_message_id"] == "prov-1"


# ---------------------------------------------------------------------------
# Live tier — SQL CHECK constraints via BEGIN/ROLLBACK
# ---------------------------------------------------------------------------


_DB_URL = os.environ.get("SUPABASE_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture
def rollback_tx():
    """Per-test psycopg connection with autocommit OFF; teardown ROLLBACKs
    every write so no row persists in prod. Skipped when DATABASE_URL absent
    or psycopg not installed."""
    if not _DB_URL:
        pytest.skip("SUPABASE_DATABASE_URL / DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(_DB_URL, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _insert_seq_step(cur, seq_name="smoke-seq", step_kw=None):
    """Create a sequence + one step row; return (seq_id, step_id)."""
    seq_id = uuid.uuid4()
    step_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO sequences (id, name, status) VALUES (%s, %s, 'active')",
        (str(seq_id), seq_name),
    )
    defaults = dict(
        channel="email",
        delay_days=0,
        delay_hours=0,
        branch_condition="always",
        send_window_start="09:00",
        send_window_end="17:00",
        send_days="mon,tue,wed,thu,fri",
    )
    defaults.update(step_kw or {})
    cur.execute(
        "INSERT INTO sequence_steps (id, sequence_id, step_index, channel,"
        " delay_days, delay_hours, branch_condition, send_window_start,"
        " send_window_end, send_days) VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s)",
        (
            str(step_id),
            str(seq_id),
            defaults["channel"],
            defaults["delay_days"],
            defaults["delay_hours"],
            defaults["branch_condition"],
            defaults["send_window_start"],
            defaults["send_window_end"],
            defaults["send_days"],
        ),
    )
    return seq_id, step_id


@pytest.mark.live
@pytest.mark.integration
class TestSequenceStepsChecks:
    def test_positive_insert_succeeds(self, rollback_tx):
        with rollback_tx.cursor() as cur:
            _insert_seq_step(cur)

    def test_send_days_int_array_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_seq_step(cur, step_kw={"send_days": "1,2,3,4,5"})

    def test_send_days_full_word_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_seq_step(cur, step_kw={"send_days": "monday,tuesday"})

    def test_window_inverted_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_seq_step(
                    cur,
                    step_kw={"send_window_start": "17:00", "send_window_end": "09:00"},
                )

    def test_unknown_channel_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_seq_step(cur, step_kw={"channel": "sms"})

    def test_negative_delay_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_seq_step(cur, step_kw={"delay_days": -1})


def _insert_variant(cur, step_id, **kw):
    defaults = dict(
        variant_label="A",
        subject_template="Hi {{ first_name }}",
        body_template="Body {{ unsubscribe_url }}",
        content_type="text",
        weight=50,
    )
    defaults.update(kw)
    cur.execute(
        "INSERT INTO sequence_variants (id, step_id, variant_label,"
        " subject_template, body_template, content_type, weight)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            str(uuid.uuid4()),
            str(step_id),
            defaults["variant_label"],
            defaults["subject_template"],
            defaults["body_template"],
            defaults["content_type"],
            defaults["weight"],
        ),
    )


@pytest.mark.live
@pytest.mark.integration
class TestSequenceVariantsChecks:
    def test_text_and_html_variants_both_insert(self, rollback_tx):
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            _insert_variant(cur, step_id, variant_label="A", content_type="text")
            _insert_variant(
                cur,
                step_id,
                variant_label="B",
                content_type="html",
                body_template="<p>{{ unsubscribe_url }}</p>",
            )

    def test_content_type_markdown_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_variant(cur, step_id, content_type="markdown")

    def test_oversize_body_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_variant(cur, step_id, body_template="x" * 16385)

    def test_oversize_subject_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_variant(cur, step_id, subject_template="s" * 999)

    def test_zero_weight_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_variant(cur, step_id, weight=0)

    def test_lowercase_label_rejected(self, rollback_tx):
        psycopg = pytest.importorskip("psycopg")
        with rollback_tx.cursor() as cur:
            _, step_id = _insert_seq_step(cur)
            with pytest.raises(psycopg.errors.CheckViolation):
                _insert_variant(cur, step_id, variant_label="a")
