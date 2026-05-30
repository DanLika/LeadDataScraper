"""General unit tests for perform_seo_audit_async in src/scrapers/seo_audit.py."""

import asyncio
import ssl
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aiohttp

from src.scrapers.seo_audit import (
    AuditFetchError,
    MAX_HTML_BYTES,
    perform_seo_audit_async,
)
from src.utils.ssrf_guard import SSRFError


def _mock_aiohttp_response(status: int, body_bytes: bytes) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.charset = "utf-8"
    response.content = MagicMock()
    response.content.read = AsyncMock(return_value=body_bytes)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    return response


def _mock_aiohttp_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestPerformSeoAuditAsync:
    @pytest.mark.asyncio
    async def test_invalid_url_types(self):
        """Test that invalid URLs return the expected red flag."""
        res_none = await perform_seo_audit_async(None)
        assert res_none["red_flags"] == ["Invalid URL"]
        assert res_none["is_up"] is False

        res_empty = await perform_seo_audit_async("")
        assert res_empty["red_flags"] == ["Invalid URL"]
        assert res_empty["is_up"] is False

        res_not_str = await perform_seo_audit_async(123)
        assert res_not_str["red_flags"] == ["Invalid URL"]
        assert res_not_str["is_up"] is False

    @pytest.mark.asyncio
    @patch("src.scrapers.seo_audit.assert_safe_scheme")
    async def test_ssrf_error_handling(self, mock_assert_safe_scheme):
        """Test that SSRF errors are caught and returned as red flags."""
        mock_assert_safe_scheme.side_effect = SSRFError("disallowed scheme")
        res = await perform_seo_audit_async("file:///etc/passwd")
        assert res["is_up"] is False
        assert "Blocked URL: disallowed scheme" in res["red_flags"]

    @pytest.mark.asyncio
    async def test_html_provided_directly(self):
        """Test providing HTML directly bypasses the network fetch and runs parsing."""
        html_content = "<html><head><title>Direct HTML</title></head><body><h1>Hello</h1><p>contact@example.com</p></body></html>"
        res = await perform_seo_audit_async("https://example.com", html=html_content)

        assert res["is_up"] is True
        assert res["title"] == "Direct HTML"
        assert res["h1_count"] == 1
        assert "contact@example.com" in res["emails"]
        assert res["response_time"] == 0.1

    @pytest.mark.asyncio
    @patch("src.scrapers.seo_audit.aiohttp.ClientSession")
    async def test_aiohttp_fetch_success(self, mock_session_class):
        """Test a successful network fetch and parsing."""
        html_content = "<html><head><title>Network HTML</title></head><body><h1>Hi</h1></body></html>"
        response_mock = _mock_aiohttp_response(200, html_content.encode("utf-8"))
        session_mock = _mock_aiohttp_session(response_mock)
        mock_session_class.return_value = session_mock

        res = await perform_seo_audit_async("example.com") # should prepend https://

        assert res["url"] == "https://example.com"
        assert res["is_up"] is True
        assert res["title"] == "Network HTML"
        assert res["has_ssl"] is True

    @pytest.mark.asyncio
    @patch("src.scrapers.seo_audit.aiohttp.ClientSession")
    async def test_aiohttp_audit_fetch_error_size_cap(self, mock_session_class):
        """Test that a body exceeding MAX_HTML_BYTES raises AuditFetchError."""
        # Create a body that is MAX_HTML_BYTES + 2 bytes
        body_bytes = b"x" * (MAX_HTML_BYTES + 2)
        response_mock = _mock_aiohttp_response(200, body_bytes)
        session_mock = _mock_aiohttp_session(response_mock)
        mock_session_class.return_value = session_mock

        # We expect AuditFetchError to be raised up, as per exception handling block
        with pytest.raises(AuditFetchError) as exc_info:
            await perform_seo_audit_async("https://example.com")

        assert "Response body exceeds" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("src.scrapers.seo_audit.aiohttp.ClientSession")
    async def test_aiohttp_ssl_error(self, mock_session_class):
        """Test that an SSLError sets has_ssl to False and adds a red flag."""
        session_mock = MagicMock()
        session_mock.get = MagicMock(side_effect=ssl.SSLError("cert verify failed"))
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = session_mock

        res = await perform_seo_audit_async("https://example.com")

        assert res["is_up"] is False
        assert res["has_ssl"] is False
        assert "SSL Certificate Error" in res["red_flags"]

    @pytest.mark.asyncio
    @patch("src.scrapers.seo_audit.aiohttp.ClientSession")
    async def test_generic_exception_connection_failed(self, mock_session_class):
        """Test that a generic Exception is caught and returns Connection Failed."""
        session_mock = MagicMock()
        session_mock.get = MagicMock(side_effect=Exception("Timeout or something"))
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = session_mock

        res = await perform_seo_audit_async("https://example.com")
        assert res["is_up"] is False
        assert any("Connection Failed: Timeout or something" in rf for rf in res["red_flags"])
