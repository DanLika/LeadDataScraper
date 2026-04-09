import sys
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

class TestDiscoveryEngine(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Mock external dependencies before importing
        cls.module_patcher = patch.dict('sys.modules', {
            'supabase': MagicMock()
        })
        cls.module_patcher.start()

        sys.path.append(os.path.abspath(os.curdir))
        global DiscoveryEngine
        from src.scrapers.discovery_engine import DiscoveryEngine

    @classmethod
    def tearDownClass(cls):
        cls.module_patcher.stop()

    @patch('src.scrapers.discovery_engine.SupabaseHelper')
    @patch('src.scrapers.discovery_engine.AgenticRouter')
    @patch('src.scrapers.discovery_engine.async_playwright')
    async def test_find_leads_empty_location(self, mock_async_playwright, mock_router, mock_supabase):
        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_async_playwright.return_value.__aenter__.return_value = mock_playwright
        mock_playwright.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.query_selector_all.return_value = []

        engine = DiscoveryEngine()

        await engine.find_leads(query="Dentist", location="")

        from urllib.parse import quote_plus
        expected_url = f"https://www.google.com/maps/search/{quote_plus('Dentist')}"
        mock_page.goto.assert_called_with(expected_url, wait_until="domcontentloaded", timeout=60000)

    @patch('src.scrapers.discovery_engine.SupabaseHelper')
    @patch('src.scrapers.discovery_engine.AgenticRouter')
    @patch('src.scrapers.discovery_engine.async_playwright')
    async def test_find_leads_none_location(self, mock_async_playwright, mock_router, mock_supabase):
        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_async_playwright.return_value.__aenter__.return_value = mock_playwright
        mock_playwright.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.query_selector_all.return_value = []

        engine = DiscoveryEngine()

        await engine.find_leads(query="Dentist", location=None)

        from urllib.parse import quote_plus
        expected_url = f"https://www.google.com/maps/search/{quote_plus('Dentist')}"
        mock_page.goto.assert_called_with(expected_url, wait_until="domcontentloaded", timeout=60000)

    @patch('src.scrapers.discovery_engine.SupabaseHelper')
    @patch('src.scrapers.discovery_engine.AgenticRouter')
    @patch('src.scrapers.discovery_engine.async_playwright')
    async def test_find_leads_with_location(self, mock_async_playwright, mock_router, mock_supabase):
        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_async_playwright.return_value.__aenter__.return_value = mock_playwright
        mock_playwright.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.query_selector_all.return_value = []

        engine = DiscoveryEngine()

        await engine.find_leads(query="Dentist", location="Miami, FL")

        from urllib.parse import quote_plus
        expected_url = f"https://www.google.com/maps/search/{quote_plus('Dentist in Miami, FL')}"
        mock_page.goto.assert_called_with(expected_url, wait_until="domcontentloaded", timeout=60000)

if __name__ == '__main__':
    unittest.main()
