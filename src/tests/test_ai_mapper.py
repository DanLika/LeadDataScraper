import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

# Mock missing dependencies
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()

from src.processors.ai_mapper import normalize_df_with_ai

class TestAIMapper(unittest.TestCase):
    def setUp(self):
        # Save original API key
        self.original_api_key = os.environ.get("GEMINI_API_KEY")
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]

    def tearDown(self):
        # Restore original API key
        if self.original_api_key is not None:
            os.environ["GEMINI_API_KEY"] = self.original_api_key
        elif "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]

    @patch("src.processors.ai_mapper.GeminiMapper")
    def test_normalize_df_with_ai_with_mapping(self, MockGeminiMapper):
        # Mock GeminiMapper and its get_column_mapping method
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_column_mapping.return_value = {
            "Company": "company_name",
            "Contact Person": "name"
        }
        MockGeminiMapper.return_value = mock_mapper_instance

        # Create sample DataFrame
        df = pd.DataFrame({
            "Company": ["Acme Corp"],
            "Contact Person": ["John Doe"],
            "Irrelevant Col": ["Ignore me"]
        })

        # Call function
        result_df = normalize_df_with_ai(df, api_key="test_api_key")

        # Verify environment variable was set
        self.assertEqual(os.environ.get("GEMINI_API_KEY"), "test_api_key")

        # Verify columns were renamed
        expected_columns = ["company_name", "name", "Irrelevant Col"]
        self.assertListEqual(list(result_df.columns), expected_columns)

        # Verify get_column_mapping was called with correct argument
        mock_mapper_instance.get_column_mapping.assert_called_once_with(["Company", "Contact Person", "Irrelevant Col"])

    @patch("src.processors.ai_mapper.GeminiMapper")
    def test_normalize_df_with_ai_empty_mapping(self, MockGeminiMapper):
        # Mock GeminiMapper to return empty mapping
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_column_mapping.return_value = {}
        MockGeminiMapper.return_value = mock_mapper_instance

        # Create sample DataFrame
        original_columns = ["Col1", "Col2"]
        df = pd.DataFrame({"Col1": [1], "Col2": [2]})

        # Call function without api_key
        result_df = normalize_df_with_ai(df)

        # Verify columns remain unchanged
        self.assertListEqual(list(result_df.columns), original_columns)

        # Verify get_column_mapping was called with correct argument
        mock_mapper_instance.get_column_mapping.assert_called_once_with(original_columns)

        # Verify environment variable was not set if not present originally
        self.assertIsNone(os.environ.get("GEMINI_API_KEY"))

if __name__ == '__main__':
    unittest.main()
