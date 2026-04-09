import sys
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.curdir))

from src.scrapers.enrichment_engine import EnrichmentEngine

class TestEnrichmentEngine(unittest.IsolatedAsyncioTestCase):

    @patch('src.scrapers.enrichment_engine.logger')
    async def test_deep_ai_parse_exception_handling(self, mock_logger):
        # Mocking out genai.Client to avoid real API calls and handle missing env vars
        with patch('src.scrapers.enrichment_engine.genai.Client') as MockClient:
            # Provide a dummy API key so EnrichmentEngine initializes its client
            with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_key"}):
                engine = EnrichmentEngine()

            # Ensure we have the client configured to raise an Exception
            engine.client.aio.models.generate_content = AsyncMock(side_effect=Exception("Test AI Exception"))

            content_blocks = ["Sample page text"]
            lead_name = "Test Lead"

            # Call the method
            result = await engine.deep_ai_parse(content_blocks, lead_name)

            # Assertions
            self.assertEqual(result, {})
            mock_logger.error.assert_called_once()

            # Validate log format
            args, kwargs = mock_logger.error.call_args
            self.assertEqual(args[0], "AI Enrichment Error for %s: %s")
            self.assertEqual(args[1], "Test Lead")
            self.assertIsInstance(args[2], Exception)
            self.assertEqual(str(args[2]), "Test AI Exception")
            self.assertEqual(kwargs, {'exc_info': True})

if __name__ == '__main__':
    unittest.main()
