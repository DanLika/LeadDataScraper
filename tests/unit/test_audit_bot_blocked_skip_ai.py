"""Regression test for Phase 9.10 (PR #274) Finding E.

Sites that return HTTP 401/403/429 (bot-block / rate-limit) used to flow
their tiny error-page body downstream to the AI pipeline. Gemini then
hallucinated plausible-but-ungrounded ``pain_points`` based on the empty
``tech_flags`` dict ("no Google Analytics, no Facebook Pixel") — inferred
from a page that never actually rendered.

After the fix, ``perform_seo_audit_async``:
  - tags the result with ``is_bot_blocked=True``,
  - records ``last_error=site_blocked_<status>``,
  - empties ``page_text`` so the existing ``if not page_text: return`` guards
    inside ``analyze_pain_points_async`` and ``enrich_business_data_async``
    short-circuit before any Gemini call.

Pure unit tests against the response-handling path using aiohttp test
mocks.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.scrapers.seo_audit import (
    _BOT_BLOCKED_STATUSES,
    _MIN_AUDITABLE_CONTENT_BYTES,
    perform_seo_audit_async,
)


def _mock_aiohttp_response(status: int, body: str) -> MagicMock:
    """Build an awaitable mock of aiohttp.ClientResponse's async context.

    Post the M6 body-cap landing in ``perform_seo_audit_async``, the
    auditor reads bytes via ``response.content.read(MAX_HTML_BYTES + 1)``
    rather than ``response.text()``. The mock therefore wires the
    ``content.read`` async method (with the body encoded to bytes) plus
    ``response.charset`` so the decode call doesn't trip on a MagicMock.
    Keeping ``response.text`` mocked is harmless — any historical caller
    still works.
    """
    response = MagicMock()
    response.status = status
    response.charset = "utf-8"
    response.text = AsyncMock(return_value=body)
    response.content = MagicMock()
    response.content.read = AsyncMock(return_value=body.encode("utf-8"))
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _mock_aiohttp_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestBotBlockedStatuses:
    """Each of the canonical bot-block statuses must trip is_bot_blocked."""

    @pytest.mark.parametrize("status", sorted(_BOT_BLOCKED_STATUSES))
    def test_blocked_status_sets_flag_and_clears_page_text(self, status: int):
        body = f"{status} Forbidden {status} Forbidden"
        response = _mock_aiohttp_response(status, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        assert result["is_bot_blocked"] is True
        assert result["last_error"] == f"site_blocked_{status}"
        assert result["page_text"] == ""  # Gemini guard fires on falsy page_text
        # The red flag is operator-visible in the audit_results JSONB.
        assert any("Bot-blocked" in flag for flag in result["red_flags"])
        # is_up semantic shift (2026-05-30): now 2xx/3xx only. 401/403/429
        # are HTTP-error states — site reachable but refused — so is_up=False
        # AND is_bot_blocked=True. The two flags are complementary, not
        # mutually exclusive.
        assert result["is_up"] is False
        # An HTTP-status red_flag joins the bot-blocked one.
        assert any(f"HTTP {status} response" in flag for flag in result["red_flags"])


class TestShortContentTrips:
    """Bodies shorter than the threshold trip the flag even on HTTP 200."""

    def test_200_with_tiny_body_treated_as_blocked(self):
        body = "ok"  # 2 bytes, well under threshold
        response = _mock_aiohttp_response(200, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        assert result["is_bot_blocked"] is True
        assert result["page_text"] == ""

    def test_threshold_constant_is_sane(self):
        # Sanity bound — operator-tunable in future, but the default should
        # remain in the hundreds-of-bytes range. A typical bot-block body
        # is <100 bytes; a real homepage is in the tens of kilobytes.
        assert 100 <= _MIN_AUDITABLE_CONTENT_BYTES <= 5000


class TestNormalPagesPassThrough:
    """A real 200 with substantial content must NOT trip the flag."""

    def test_normal_homepage_does_not_set_bot_blocked(self):
        # ~3 kB of plausible-looking marketing copy.
        body = (
            "<html><head><title>Acme Co — Plumbing Services</title>"
            "<meta name='description' content='High-quality plumbing in the Tri-State area for 30 years.'>"
            "</head><body><h1>Welcome to Acme</h1>"
            + (
                "<p>We do plumbing repairs, drain cleaning, water heater install, and more.</p>"
                * 30
            )
            + "</body></html>"
        )
        assert len(body) > _MIN_AUDITABLE_CONTENT_BYTES
        response = _mock_aiohttp_response(200, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        # Flag not set; page_text was extracted.
        assert result.get("is_bot_blocked") is not True
        assert result["page_text"]  # non-empty
        assert result["title"] == "Acme Co — Plumbing Services"


class TestStatusValuesAreSensible:
    """The blocked-status set is the documented one (401, 403, 429)."""

    def test_405_method_not_allowed_not_treated_as_blocked(self):
        # 405 is "method wrong" — generally not a bot-block. We don't
        # silently expand the blocklist; that would mask real bugs.
        body = "Method Not Allowed for GET"
        # padding so it doesn't trip the length threshold either.
        body = body + (" filler" * 200)
        response = _mock_aiohttp_response(405, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))
        assert result.get("is_bot_blocked") is not True
        # is_up=False because 405 is a 4xx (post 2026-05-30 semantic).
        assert result["is_up"] is False
        assert result["last_error"] == "HTTP 405"

    def test_403_in_set(self):
        assert 403 in _BOT_BLOCKED_STATUSES

    def test_500_not_in_set(self):
        # 500 (server error) is a different failure mode — not "you're a
        # bot" — and should fall through to existing error handling.
        assert 500 not in _BOT_BLOCKED_STATUSES


class TestIsUpHttpStatusGate:
    """2026-05-30 fix: is_up = (200 <= status < 400). 4xx/5xx pages
    no longer flow their error-template HTML through the line-402
    analysis block in ``perform_seo_audit_async`` — keeps bogus
    tech_flags + seo_score + segment_lead inputs out of the pipeline.

    Pre-fix: is_up=True for every status. Operator decision: do not
    backfill historical leads with corrupted ``audit_results`` — the
    next re-audit overwrites them, and outreach_score is independent
    of seo_score (CLAUDE.md pinned finding #1).
    """

    @pytest.mark.parametrize("status", [404, 410, 500, 502, 503, 504])
    def test_4xx_5xx_marks_is_up_false_and_skips_analysis(self, status: int):
        # Body must be >= _MIN_AUDITABLE_CONTENT_BYTES so the bot-blocked
        # thin-body branch does NOT fire — we want to test the pure
        # HTTP-status gate, not the (still-correct) thin-body overlap.
        body = (
            f"<html><body><h1>HTTP {status} error</h1>"
            + ("<p>verbose error template padding</p>" * 50)
            + "</body></html>"
        )
        assert len(body) >= _MIN_AUDITABLE_CONTENT_BYTES
        response = _mock_aiohttp_response(status, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        assert result["is_up"] is False
        assert result["http_status"] == status
        assert result["last_error"] == f"HTTP {status}"
        assert any(f"HTTP {status} response" in f for f in result["red_flags"])
        # Analysis block must have been skipped — default zero score,
        # default tech_flags, no title parsed from the error template.
        assert result["score"] == 0
        assert result["title"] is None
        assert result["meta_description"] is None
        # tech_flags untouched from the all-False initial dict.
        assert all(v is False for v in result["tech_flags"].values())
        # bot-blocked stays False — 404/500/etc are HTTP errors, not
        # anti-bot soft-blocks.
        assert result.get("is_bot_blocked") is not True

    @pytest.mark.parametrize("status", [200, 201, 204, 301, 302, 304])
    def test_2xx_3xx_keeps_is_up_true(self, status: int):
        body = (
            "<html><head><title>Normal Page</title></head><body><h1>OK</h1>"
            + ("<p>normal homepage content padding</p>" * 50)
            + "</body></html>"
        )
        response = _mock_aiohttp_response(status, body)
        session = _mock_aiohttp_session(response)
        with patch(
            "src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session
        ):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        assert result["is_up"] is True
        assert result["http_status"] == status
        # No HTTP-error last_error on success statuses.
        assert "HTTP " not in (result.get("last_error") or "")
