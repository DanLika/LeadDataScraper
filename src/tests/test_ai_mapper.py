import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.processors.ai_mapper import GeminiMapper

class TestGeminiMapper(unittest.TestCase):
    def test_get_column_mapping_success(self):
        """Test that get_column_mapping returns correctly parsed json."""
        mapper = GeminiMapper()
        mapper.client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"Company": "name"}'
        mapper.client.models.generate_content.return_value = mock_response

        mapping = mapper.get_column_mapping(["Company"])
        self.assertEqual(mapping, {"Company": "name"})

    def test_get_column_mapping_exception(self):
        """Test that get_column_mapping handles exceptions gracefully, returning empty dict."""
        mapper = GeminiMapper()
        mapper.client = MagicMock()
        mapper.client.models.generate_content.side_effect = Exception("API error")

        mapping = mapper.get_column_mapping(["Company"])
        self.assertEqual(mapping, {})

if __name__ == '__main__':
    unittest.main()
