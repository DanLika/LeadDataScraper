import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Ensure project root is in the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.scrapers.enrichment_engine import EnrichmentEngine

class TestEnrichmentEngineInit(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    @patch('src.scrapers.enrichment_engine.logger.warning')
    def test_init_missing_api_key(self, mock_logger_warning):
        """Test EnrichmentEngine initialization when GEMINI_API_KEY is missing."""
        # Because we clear os.environ, os.getenv("GEMINI_API_KEY") will return None
        engine = EnrichmentEngine()

        self.assertIsNone(engine.client)
        self.assertIsNone(engine.api_key)
        mock_logger_warning.assert_called_once_with("GEMINI_API_KEY not found. AI features will be disabled.")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_fake_api_key"})
    @patch('src.scrapers.enrichment_engine.genai.Client')
    def test_init_with_api_key(self, mock_genai_client):
        """Test EnrichmentEngine initialization when GEMINI_API_KEY is present."""
        engine = EnrichmentEngine()

        self.assertEqual(engine.api_key, "test_fake_api_key")
        mock_genai_client.assert_called_once_with(api_key="test_fake_api_key")
        self.assertEqual(engine.client, mock_genai_client.return_value)

if __name__ == '__main__':
    unittest.main()
