import unittest
import asyncio
from unittest.mock import AsyncMock, patch
import sys
import os

sys.path.append(os.path.abspath(os.curdir))

from src.scrapers.enrichment_engine import EnrichmentEngine

class TestEnrichmentEngineExtractPageContent(unittest.IsolatedAsyncioTestCase):
    async def test_extract_page_content_success(self):
        engine = EnrichmentEngine()
        with patch('src.scrapers.enrichment_engine.async_playwright') as mock_ap:
            mock_playwright = AsyncMock()
            mock_ap.return_value.__aenter__.return_value = mock_playwright
            mock_browser = AsyncMock()
            mock_playwright.chromium.launch.return_value = mock_browser
            mock_context = AsyncMock()
            mock_browser.new_context.return_value = mock_context
            mock_page = AsyncMock()
            mock_context.new_page.return_value = mock_page

            mock_page.evaluate.return_value = "This is a test content"

            result = await engine.extract_page_content("http://example.com")

            self.assertEqual(result, "This is a test content")
            mock_page.goto.assert_called_once_with("http://example.com", wait_until="domcontentloaded", timeout=45000)
            mock_page.evaluate.assert_called_once_with("() => document.body.innerText")
            mock_context.close.assert_called_once()
            mock_browser.close.assert_called_once()

    async def test_extract_page_content_truncation(self):
        engine = EnrichmentEngine()
        with patch('src.scrapers.enrichment_engine.async_playwright') as mock_ap:
            mock_playwright = AsyncMock()
            mock_ap.return_value.__aenter__.return_value = mock_playwright
            mock_browser = AsyncMock()
            mock_playwright.chromium.launch.return_value = mock_browser
            mock_context = AsyncMock()
            mock_browser.new_context.return_value = mock_context
            mock_page = AsyncMock()
            mock_context.new_page.return_value = mock_page

            long_content = "A" * 15000
            mock_page.evaluate.return_value = long_content

            result = await engine.extract_page_content("http://example.com")

            self.assertEqual(len(result), 10000)
            self.assertEqual(result, "A" * 10000)

    async def test_extract_page_content_timeout(self):
        engine = EnrichmentEngine()
        with patch('src.scrapers.enrichment_engine.async_playwright') as mock_ap:
            mock_playwright = AsyncMock()
            mock_ap.return_value.__aenter__.return_value = mock_playwright
            mock_browser = AsyncMock()
            mock_playwright.chromium.launch.return_value = mock_browser
            mock_context = AsyncMock()
            mock_browser.new_context.return_value = mock_context
            mock_page = AsyncMock()
            mock_context.new_page.return_value = mock_page

            # Simulate a timeout on page.goto
            mock_page.goto.side_effect = asyncio.TimeoutError()

            result = await engine.extract_page_content("http://example.com")

            self.assertEqual(result, "")
            mock_context.close.assert_called_once()
            mock_browser.close.assert_called_once()

    async def test_extract_page_content_navigation_error(self):
        engine = EnrichmentEngine()
        with patch('src.scrapers.enrichment_engine.async_playwright') as mock_ap:
            mock_playwright = AsyncMock()
            mock_ap.return_value.__aenter__.return_value = mock_playwright
            mock_browser = AsyncMock()
            mock_playwright.chromium.launch.return_value = mock_browser
            mock_context = AsyncMock()
            mock_browser.new_context.return_value = mock_context
            mock_page = AsyncMock()
            mock_context.new_page.return_value = mock_page

            # Simulate a generic exception
            mock_page.goto.side_effect = Exception("Connection refused")

            result = await engine.extract_page_content("http://example.com")

            self.assertEqual(result, "")
            mock_context.close.assert_called_once()
            mock_browser.close.assert_called_once()

    async def test_extract_page_content_browser_launch_error(self):
        engine = EnrichmentEngine()
        with patch('src.scrapers.enrichment_engine.async_playwright') as mock_ap:
            mock_playwright = AsyncMock()
            mock_ap.return_value.__aenter__.return_value = mock_playwright

            mock_playwright.chromium.launch.side_effect = Exception("Browser launch failed")

            result = await engine.extract_page_content("http://example.com")

            self.assertEqual(result, "")

if __name__ == '__main__':
    unittest.main()
