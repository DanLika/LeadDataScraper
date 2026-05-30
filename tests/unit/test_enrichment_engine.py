import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from src.scrapers.enrichment_engine import EnrichmentEngine
from src.utils.ssrf_guard import SSRFError

class TestEnrichmentEngineFetchPage(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_page_basic(self):
        engine = EnrichmentEngine()
        engine.browser_semaphore = MagicMock()
        engine.browser_semaphore.__aenter__ = AsyncMock()
        engine.browser_semaphore.__aexit__ = AsyncMock()
        engine._get_browser = AsyncMock()
        browser = AsyncMock()
        engine._get_browser.return_value = browser
        context = AsyncMock()
        browser.new_context.return_value = context
        page = AsyncMock()
        context.new_page.return_value = page
        page.evaluate.return_value = "This is a test content that is long enough." * 10

        with patch('src.scrapers.enrichment_engine._install_ssrf_route_guard', new_callable=AsyncMock) as mock_install, \
             patch('src.scrapers.enrichment_engine.assert_safe_url', new_callable=AsyncMock) as mock_safe:
            lead = {"name": "Test", "website": "http://example.com"}

            # Use mock to avoid AI parsing
            with patch.object(engine, 'deep_ai_parse', new_callable=AsyncMock) as mock_parse:
                mock_parse.return_value = {"company_name": "Test Company"}

                result = await engine.enrich_lead(lead)

                self.assertEqual(result["enrichment_status"], "COMPLETED")
                self.assertEqual(result["company_name"], "Test Company")

                page.goto.assert_called_once()
                page.evaluate.assert_called_once()

    async def test_fetch_page_ssrf_error(self):
        engine = EnrichmentEngine()
        engine.browser_semaphore = MagicMock()
        engine.browser_semaphore.__aenter__ = AsyncMock()
        engine.browser_semaphore.__aexit__ = AsyncMock()
        engine._get_browser = AsyncMock()
        browser = AsyncMock()
        engine._get_browser.return_value = browser
        context = AsyncMock()
        browser.new_context.return_value = context
        page = AsyncMock()
        context.new_page.return_value = page
        page.evaluate.return_value = "This is a test content that is long enough." * 10

        with patch('src.scrapers.enrichment_engine._install_ssrf_route_guard', new_callable=AsyncMock) as mock_install, \
             patch('src.scrapers.enrichment_engine.assert_safe_url', new_callable=AsyncMock) as mock_safe:
            mock_safe.side_effect = SSRFError("Blocked")
            lead = {"name": "Test", "website": "http://example.com"}

            result = await engine.enrich_lead(lead)

            self.assertEqual(result["enrichment_status"], "FAILED_NO_CONTENT")

    async def test_fetch_page_invalid_url(self):
        engine = EnrichmentEngine()
        engine.browser_semaphore = MagicMock()
        engine.browser_semaphore.__aenter__ = AsyncMock()
        engine.browser_semaphore.__aexit__ = AsyncMock()
        engine._get_browser = AsyncMock()
        browser = AsyncMock()
        engine._get_browser.return_value = browser
        context = AsyncMock()
        browser.new_context.return_value = context

        with patch('src.scrapers.enrichment_engine._install_ssrf_route_guard', new_callable=AsyncMock) as mock_install, \
             patch('src.scrapers.enrichment_engine.assert_safe_url', new_callable=AsyncMock) as mock_safe:
            # Not http or https
            lead = {"name": "Test", "website": "ftp://example.com"}

            result = await engine.enrich_lead(lead)
            self.assertEqual(result["enrichment_status"], "FAILED_NO_CONTENT")
            mock_safe.assert_not_called()

    async def test_fetch_page_navigation_timeout(self):
        engine = EnrichmentEngine()
        engine.browser_semaphore = MagicMock()
        engine.browser_semaphore.__aenter__ = AsyncMock()
        engine.browser_semaphore.__aexit__ = AsyncMock()
        engine._get_browser = AsyncMock()
        browser = AsyncMock()
        engine._get_browser.return_value = browser
        context = AsyncMock()
        browser.new_context.return_value = context
        page = AsyncMock()
        context.new_page.return_value = page

        # Simulate timeout on page.goto
        page.goto.side_effect = Exception("Timeout")

        with patch('src.scrapers.enrichment_engine._install_ssrf_route_guard', new_callable=AsyncMock) as mock_install, \
             patch('src.scrapers.enrichment_engine.assert_safe_url', new_callable=AsyncMock) as mock_safe:
            lead = {"name": "Test", "website": "http://example.com"}

            result = await engine.enrich_lead(lead)

            self.assertEqual(result["enrichment_status"], "FAILED_NO_CONTENT")

    async def test_fetch_page_no_url(self):
        engine = EnrichmentEngine()
        engine.browser_semaphore = MagicMock()
        engine.browser_semaphore.__aenter__ = AsyncMock()
        engine.browser_semaphore.__aexit__ = AsyncMock()
        engine._get_browser = AsyncMock()
        browser = AsyncMock()
        engine._get_browser.return_value = browser
        context = AsyncMock()
        browser.new_context.return_value = context

        with patch('src.scrapers.enrichment_engine._install_ssrf_route_guard', new_callable=AsyncMock) as mock_install, \
             patch('src.scrapers.enrichment_engine.assert_safe_url', new_callable=AsyncMock) as mock_safe:
            # Missing website, team_url, clients_url, about_url
            lead = {"name": "Test"}

            result = await engine.enrich_lead(lead)
            # Result doesn't change since we check if not urls_to_check at the beginning
            self.assertEqual(result, lead)

if __name__ == '__main__':
    unittest.main()
