"""Unit tests for ``src/utils/gemini_call.py`` — the wrapper helpers
that fence every Gemini call behind ``check_budget`` + ``record_usage``.

These tests mock the Gemini client entirely; no API calls fire.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils import gemini_call  # noqa: E402
from src.utils.gemini_budget import BudgetExceededError  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_real_budget_gate(monkeypatch):
    """The suite-wide ``tests/conftest.py`` neuters
    ``gemini_call.check_budget`` + ``record_usage`` to no-ops so other
    tests don't trip the daily-token ceiling. This file *exercises*
    the gate itself — restore the originals (stashed on the module by
    the conftest as ``_real_check_budget`` / ``_real_record_usage``)
    for the duration of each test in this file, then ``monkeypatch``
    auto-rolls back on teardown.
    """
    real_check = getattr(gemini_call, "_real_check_budget", None)
    real_record = getattr(gemini_call, "_real_record_usage", None)
    if real_check is not None:
        monkeypatch.setattr(gemini_call, "check_budget", real_check)
    if real_record is not None:
        monkeypatch.setattr(gemini_call, "record_usage", real_record)


@pytest.fixture
def isolated_budget(tmp_path, monkeypatch):
    """Per-test isolated SQLite + permissive default ceiling."""
    monkeypatch.setenv("GEMINI_BUDGET_DB", str(tmp_path / "budget.db"))
    monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "1000000")
    return tmp_path


def _fake_response(prompt_tokens: int, candidates_tokens: int):
    """Build a SimpleNamespace mimicking the Gemini SDK response shape."""
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
        ),
        text="ok",
    )


def _fake_response_no_metadata():
    """Older SDKs or mocked clients may not surface usage_metadata."""
    return SimpleNamespace(text="ok")


class TestEstimateTokensFromText:
    def test_returns_zero_on_empty_input(self):
        assert gemini_call.estimate_tokens_from_text("") == 0
        assert gemini_call.estimate_tokens_from_text(None) == 0  # type: ignore[arg-type]

    def test_returns_byte_len_div_4(self):
        # 100 ascii chars → 100 bytes → 25 tokens.
        assert gemini_call.estimate_tokens_from_text("a" * 100) == 25

    def test_multibyte_chars_are_byte_counted(self):
        # Each "ž" is 2 bytes utf-8 → 200 bytes → 50 tokens.
        assert gemini_call.estimate_tokens_from_text("ž" * 100) == 50


class TestGuardedGenerateContentSync:
    def test_happy_path_calls_check_and_record(self, isolated_budget):
        from src.utils import gemini_budget

        # Mock client.models.generate_content to return a usage-bearing
        # response.  We capture the call args + usage to verify the
        # record_usage delta is computed off the SDK numbers.
        client = MagicMock()
        client.models.generate_content.return_value = _fake_response(
            prompt_tokens=123,
            candidates_tokens=456,
        )

        response = gemini_call.guarded_generate_content(
            client,
            model="gemini-flash-latest",
            contents="hello",
            config="cfg-sentinel",
            estimate_input=100,
            estimate_output=400,
        )
        assert response.text == "ok"
        client.models.generate_content.assert_called_once_with(
            model="gemini-flash-latest",
            contents="hello",
            config="cfg-sentinel",
        )
        # Real usage (123/456) should be reflected in the row, not the
        # estimate (100/400).
        state = gemini_budget.get_state()
        assert state["input_today"] == 123
        assert state["output_today"] == 456

    def test_budget_exceeded_skips_sdk_call(self, isolated_budget, monkeypatch):
        """If check_budget raises, the Gemini client must NEVER be
        invoked — we don't want to charge for a call we're about to
        reject anyway."""
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "500")

        client = MagicMock()
        # First call burns the budget fully.
        client.models.generate_content.return_value = _fake_response(250, 250)
        gemini_call.guarded_generate_content(
            client,
            model="m",
            contents="prime",
            config=None,
            estimate_input=250,
            estimate_output=250,
        )
        # Second call must raise BEFORE the SDK fires.
        client.models.generate_content.reset_mock()
        with pytest.raises(BudgetExceededError):
            gemini_call.guarded_generate_content(
                client,
                model="m",
                contents="second",
                config=None,
                estimate_input=1,
                estimate_output=1,
            )
        client.models.generate_content.assert_not_called()

    def test_missing_usage_metadata_falls_back_to_estimate(self, isolated_budget):
        from src.utils import gemini_budget

        client = MagicMock()
        client.models.generate_content.return_value = _fake_response_no_metadata()
        gemini_call.guarded_generate_content(
            client,
            model="m",
            contents="x",
            config=None,
            estimate_input=42,
            estimate_output=17,
        )
        # Fallback: counters carry the estimate, not zero (so a missing-
        # metadata response doesn't appear free).
        state = gemini_budget.get_state()
        assert state["input_today"] == 42
        assert state["output_today"] == 17

    def test_negative_estimates_clamped_to_zero(self, isolated_budget):
        client = MagicMock()
        client.models.generate_content.return_value = _fake_response(0, 0)
        # Should not raise even if caller passed negative estimates.
        gemini_call.guarded_generate_content(
            client,
            model="m",
            contents="x",
            config=None,
            estimate_input=-5,
            estimate_output=-3,
        )

    def test_sdk_exception_propagates(self, isolated_budget):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("network down")
        with pytest.raises(RuntimeError, match="network down"):
            gemini_call.guarded_generate_content(
                client,
                model="m",
                contents="x",
                config=None,
                estimate_input=10,
                estimate_output=10,
            )


class TestGuardedGenerateContentAsync:
    @pytest.mark.asyncio
    async def test_happy_path(self, isolated_budget):
        from src.utils import gemini_budget

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=_fake_response(50, 75)
        )
        await gemini_call.guarded_generate_content_async(
            client,
            model="m",
            contents="hello",
            config=None,
            estimate_input=40,
            estimate_output=80,
        )
        client.aio.models.generate_content.assert_awaited_once_with(
            model="m",
            contents="hello",
            config=None,
        )
        state = gemini_budget.get_state()
        # estimate 40/80 was pre-debited; actual was 50/75.
        # input: actual > estimate → counter catches up to 50.
        # output: actual < estimate → MONOTONIC INVARIANT (Phase 9.10
        # Finding H): counter does NOT decrement, stays at the pre-debit
        # of 80 even though the real spend was 75. See
        # src/utils/gemini_budget.py::record_usage. Trade-off: counter
        # may over-state usage when estimates are sloppy. That is the
        # safer direction — better to false-trip the ceiling than to
        # silently overspend (the original buggy direction).
        assert state["input_today"] == 50
        assert state["output_today"] == 80

    @pytest.mark.asyncio
    async def test_budget_exceeded_skips_async_sdk_call(
        self, isolated_budget, monkeypatch
    ):
        monkeypatch.setenv("GEMINI_DAILY_TOKEN_CEILING", "100")
        # First call burns budget.
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=_fake_response(50, 50)
        )
        await gemini_call.guarded_generate_content_async(
            client,
            model="m",
            contents="prime",
            config=None,
            estimate_input=50,
            estimate_output=50,
        )
        client.aio.models.generate_content.reset_mock()

        with pytest.raises(BudgetExceededError):
            await gemini_call.guarded_generate_content_async(
                client,
                model="m",
                contents="second",
                config=None,
                estimate_input=1,
                estimate_output=1,
            )
        client.aio.models.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_usage_metadata_async(self, isolated_budget):
        from src.utils import gemini_budget

        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=_fake_response_no_metadata()
        )
        await gemini_call.guarded_generate_content_async(
            client,
            model="m",
            contents="x",
            config=None,
            estimate_input=11,
            estimate_output=22,
        )
        state = gemini_budget.get_state()
        assert state["input_today"] == 11
        assert state["output_today"] == 22


class TestExtractUsageEdgeCases:
    """Defense in depth on the _extract_usage helper."""

    def test_non_int_metadata_falls_back(self):
        r = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count="not-an-int",
                candidates_token_count=None,
            ),
        )
        a, b = gemini_call._extract_usage(r, 5, 7)
        assert a == 5 and b == 7

    def test_zero_metadata_uses_fallback(self):
        # A response with prompt_token_count=0 means the SDK didn't
        # populate it (e.g. mock).  Use the estimate so we don't
        # silently revert the pre-debit to zero.
        r = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=0,
                candidates_token_count=0,
            ),
        )
        a, b = gemini_call._extract_usage(r, 42, 17)
        assert a == 42 and b == 17
