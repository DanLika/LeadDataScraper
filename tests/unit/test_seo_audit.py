"""Tests for the SEO auditor."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.scrapers.seo_audit import (
    calculate_seo_score,
    perform_seo_audit_async,
)
from src.errors import AuditFetchError
from src.utils.ssrf_guard import SSRFError

def _mock_aiohttp_response(status: int, body: str) -> MagicMock:
    """Build an awaitable mock of aiohttp.ClientResponse's async context."""
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

class TestCalculateSeoScore:
    def test_calculate_seo_score_perfect(self):
        results = {
            "has_ssl": True,
            "title": "A perfect title",
            "meta_description": "A perfect description",
            "h1_count": 1,
            "tech_flags": {
                "has_viewport": True,
                "has_google_analytics": True,
                "has_facebook_pixel": True,
                "has_robots_txt": True,
                "has_sitemap": True,
            },
            "response_time": 1.5,
            "red_flags": [],
        }
        score = calculate_seo_score(results)
        assert score == 100

    def test_calculate_seo_score_empty(self):
        results = {}
        score = calculate_seo_score(results)
        # response_time default 0 < 2.0 -> +10
        # red_flags default [] == 0 -> +10
        # total: 20
        assert score == 20

    def test_calculate_seo_score_partial(self):
        results = {
            "has_ssl": True,
            "title": "A perfect title",
            "meta_description": None,
            "h1_count": 0,
            "tech_flags": {
                "has_viewport": True,
                "has_google_analytics": False,
                "has_facebook_pixel": False,
                "has_robots_txt": True,
                "has_sitemap": False,
            },
            "response_time": 3.0,
            "red_flags": ["Missing Meta Description", "Missing H1 Header"],
        }
        score = calculate_seo_score(results)
        # SSL: 10, title: 10
        # viewport: 10
        # total: 30
        assert score == 30


class TestPerformSeoAuditAsync:
    def test_perform_seo_audit_invalid_url(self):
        result = asyncio.run(perform_seo_audit_async(None))
        assert result["is_up"] is False
        assert "Invalid URL" in result["red_flags"]
        assert result["score"] == 0

    @patch("src.scrapers.seo_audit.assert_safe_scheme")
    def test_perform_seo_audit_ssrf_error(self, mock_assert_safe):
        mock_assert_safe.side_effect = SSRFError("disallowed scheme")
        result = asyncio.run(perform_seo_audit_async("ftp://example.com"))
        assert result["is_up"] is False
        assert any("Blocked URL" in flag for flag in result["red_flags"])

    def test_perform_seo_audit_success(self):
        body = (
            "<html><head><title>Test Page</title>"
            "<meta name='description' content='A valid description long enough to pass the check! A valid description long enough to pass the check!'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "</head><body><h1>Test Header</h1>"
            "<a href='https://facebook.com/test'>Facebook</a>"
            "</body></html>"
        )
        response = _mock_aiohttp_response(200, body)
        session = _mock_aiohttp_session(response)

        with patch("src.scrapers.seo_audit.aiohttp.ClientSession", return_value=session):
            result = asyncio.run(perform_seo_audit_async("https://example.com"))

        assert result["is_up"] is True
        assert result["title"] == "Test Page"
        assert result["has_ssl"] is True
        assert result["h1_count"] == 1
        assert result["facebook"] == "https://facebook.com/test"
        assert result["score"] > 0
