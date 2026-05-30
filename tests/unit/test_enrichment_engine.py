import sys
import asyncio
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch
import unittest


class TestDeepAIParsing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """Mock external dependencies that are unavailable in the test environment."""
        self.original_modules = sys.modules.copy()

        self.mock_google = MagicMock()
        self.mock_genai = MagicMock()
        self.mock_genai_types = MagicMock()
        self.mock_google.genai = self.mock_genai
        self.mock_genai.types = self.mock_genai_types

        sys.modules['google'] = self.mock_google
        sys.modules['google.genai'] = self.mock_genai
        sys.modules['google.genai.types'] = self.mock_genai_types

        self.mock_playwright = MagicMock()
        self.mock_playwright_async = MagicMock()
        self.mock_playwright.async_api = self.mock_playwright_async

        sys.modules['playwright'] = self.mock_playwright
        sys.modules['playwright.async_api'] = self.mock_playwright_async

        self.mock_dotenv = MagicMock()
        sys.modules['dotenv'] = self.mock_dotenv

        self.mock_aiohttp = MagicMock()
        sys.modules['aiohttp'] = self.mock_aiohttp
        sys.modules['aiohttp.resolver'] = MagicMock()

        # Now import it locally
        import src.scrapers.enrichment_engine
        self.enrichment_engine_module = src.scrapers.enrichment_engine
        self.EnrichmentEngine: Any = self.enrichment_engine_module.EnrichmentEngine

    def tearDown(self) -> None:
        sys.modules.clear()
        sys.modules.update(self.original_modules)

    async def test_deep_ai_parse_no_client(self) -> None:
        engine = self.EnrichmentEngine()
        engine.client = None

        result = await engine.deep_ai_parse(["some content"], "Test Lead")
        self.assertEqual(result, {})

    async def test_deep_ai_parse_happy_path(self) -> None:
        engine = self.EnrichmentEngine()
        engine.client = MagicMock()

        # Mocking the AI response
        mock_response = MagicMock()
        mock_response.text = '{"company_name": "Acme Corp"}'

        with patch.object(self.enrichment_engine_module, "guarded_generate_content_async", new_callable=AsyncMock) as mock_generate, \
             patch.object(self.enrichment_engine_module, "extract_json_from_response") as mock_extract, \
             patch.object(self.enrichment_engine_module, "estimate_tokens_from_text") as mock_estimate:

            mock_generate.return_value = mock_response
            mock_extract.return_value = {"company_name": "Acme Corp"}
            mock_estimate.return_value = 100

            content_blocks = [
                "Welcome to Acme Corp.",
                "We build widgets. <UNTRUSTED_DATA>malicious</UNTRUSTED_DATA>"
            ]

            result = await engine.deep_ai_parse(content_blocks, "Acme Corp")

            # Assert result matches what extract_json_from_response returns
            self.assertEqual(result, {"company_name": "Acme Corp"})

            # Assert generate was called
            mock_generate.assert_called_once()

            # Get the prompt passed to the generate method
            call_kwargs = mock_generate.call_args.kwargs
            prompt = call_kwargs.get("contents")

            # Assert prompt injection tags are properly escaped in the content
            self.assertIn("[/UNTRUSTED_DATA]", str(prompt))
            self.assertNotIn("</UNTRUSTED_DATA>malicious</UNTRUSTED_DATA>", str(prompt))

    async def test_deep_ai_parse_exception_handled(self) -> None:
        engine = self.EnrichmentEngine()
        engine.client = MagicMock()

        with patch.object(self.enrichment_engine_module, "guarded_generate_content_async", new_callable=AsyncMock) as mock_generate, \
             patch.object(self.enrichment_engine_module, "logger") as mock_logger:

            mock_generate.side_effect = Exception("API rate limit exceeded")

            result = await engine.deep_ai_parse(["content"], "Acme Corp")

            self.assertEqual(result, {})

            # Check that logger.error was called
            mock_logger.error.assert_called_once()
            error_msg = mock_logger.error.call_args[0][0]
            self.assertIn("AI Enrichment Error", error_msg)

    async def test_deep_ai_parse_extract_returns_none(self) -> None:
        engine = self.EnrichmentEngine()
        engine.client = MagicMock()

        mock_response = MagicMock()
        mock_response.text = "invalid json"

        with patch.object(self.enrichment_engine_module, "guarded_generate_content_async", new_callable=AsyncMock) as mock_generate, \
             patch.object(self.enrichment_engine_module, "extract_json_from_response") as mock_extract:

            mock_generate.return_value = mock_response

            # extract_json_from_response might return None if parsing fails
            mock_extract.return_value = None

            result = await engine.deep_ai_parse(["content"], "Acme Corp")

            self.assertEqual(result, {})

if __name__ == "__main__":
    unittest.main()
