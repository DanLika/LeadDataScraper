import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from src.scrapers.enrichment_engine import EnrichmentEngine
from src.utils.ssrf_guard import SSRFError

class TestEnrichmentEngineExtractPageContent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # We don't need a real GEMINI API key for these tests
        with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_key"}):
            self.engine = EnrichmentEngine()

        # Common mocks setup to be used across tests
        self.mock_browser = AsyncMock()
        self.mock_context = AsyncMock()
        self.mock_page = AsyncMock()

        self.mock_browser.new_context.return_value = self.mock_context
        self.mock_context.new_page.return_value = self.mock_page

    async def test_extract_page_content_success(self):
        # Arrange
        url = "https://example.com"
        expected_text = "a" * 15000  # Longer than the 10000 cap
        self.mock_page.evaluate.return_value = expected_text

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock) as mock_assert_safe_url, \
             patch.object(self.engine, "_get_browser", return_value=self.mock_browser) as mock_get_browser, \
             patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock) as mock_install_guard:

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            mock_assert_safe_url.assert_awaited_once_with(url)
            mock_get_browser.assert_awaited_once()
            self.mock_browser.new_context.assert_awaited_once()
            mock_install_guard.assert_awaited_once_with(self.mock_context)
            self.mock_context.new_page.assert_awaited_once()

            # verify wait_for / goto
            self.mock_page.goto.assert_awaited_once_with(url, wait_until="domcontentloaded", timeout=45000)

            # verify text cap
            self.assertEqual(len(result), 10000)
            self.assertEqual(result, expected_text[:10000])

            # verify finally close
            self.mock_context.close.assert_awaited_once()

    async def test_extract_page_content_ssrf_blocked(self):
        # Arrange
        url = "http://169.254.169.254/latest/meta-data/"

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock) as mock_assert_safe_url, \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger, \
             patch.object(self.engine, "_get_browser") as mock_get_browser:

            mock_assert_safe_url.side_effect = SSRFError("Blocked")

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            self.assertEqual(result, "")
            mock_logger.warning.assert_called_once_with("Blocked extract_page_content URL %s: %s", url, mock_assert_safe_url.side_effect)
            mock_get_browser.assert_not_called()

    async def test_extract_page_content_browser_launch_failure(self):
        # Arrange
        url = "https://example.com"

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger, \
             patch.object(self.engine, "_get_browser", new_callable=AsyncMock) as mock_get_browser:

            mock_get_browser.side_effect = Exception("Browser Launch Failed")

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            self.assertEqual(result, "")
            mock_logger.error.assert_called_once_with("Browser launch failed: %s", mock_get_browser.side_effect, exc_info=True)

    async def test_extract_page_content_context_creation_failure(self):
        # Arrange
        url = "https://example.com"

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
             patch.object(self.engine, "_get_browser", return_value=self.mock_browser), \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger:

            self.mock_browser.new_context.side_effect = Exception("Context Creation Failed")

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            self.assertEqual(result, "")
            mock_logger.error.assert_called_once_with("Browser enrichment context error: %s", self.mock_browser.new_context.side_effect)

    async def test_extract_page_content_navigation_timeout(self):
        # Arrange
        url = "https://example.com"

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
             patch.object(self.engine, "_get_browser", return_value=self.mock_browser), \
             patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock), \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger:

            # Simulate a TimeoutError from asyncio.wait_for
            self.mock_page.goto.side_effect = asyncio.TimeoutError("Timeout")

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            self.assertEqual(result, "")
            mock_logger.warning.assert_called_once_with("Enrichment Timeout: Operation took > 50s for %s", url)
            self.mock_context.close.assert_awaited_once()

    async def test_extract_page_content_navigation_error(self):
        # Arrange
        url = "https://example.com"

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
             patch.object(self.engine, "_get_browser", return_value=self.mock_browser), \
             patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock), \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger:

            # Simulate a generic error from page.goto
            test_exception = Exception("Navigation Failed")
            self.mock_page.goto.side_effect = test_exception

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            self.assertEqual(result, "")
            mock_logger.error.assert_called_once_with("Navigation/Content error for %s: %s", url, test_exception)
            self.mock_context.close.assert_awaited_once()

    async def test_extract_page_content_context_close_error(self):
        # Arrange
        url = "https://example.com"
        expected_text = "Some text content"
        self.mock_page.evaluate.return_value = expected_text

        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
             patch.object(self.engine, "_get_browser", return_value=self.mock_browser), \
             patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock), \
             patch("src.scrapers.enrichment_engine.logger") as mock_logger:

            # Simulate an error during context.close()
            test_exception = Exception("Close Failed")
            self.mock_context.close.side_effect = test_exception

            # Act
            result = await self.engine.extract_page_content(url)

            # Assert
            # Content should still be returned
            self.assertEqual(result, expected_text)
            # Warning should be logged
            mock_logger.warning.assert_called_once_with("Context close raised: %s", test_exception)
            self.mock_context.close.assert_awaited_once()

if __name__ == "__main__":
    unittest.main()
