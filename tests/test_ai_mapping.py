import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

sys.path.append(os.path.abspath(os.curdir))

from backend.main import _apply_ai_mapping

class TestApplyAIMapping(unittest.TestCase):
    @patch('backend.main.logger')
    @patch('src.processors.ai_mapper.GeminiMapper')
    def test_apply_ai_mapping_with_mapping(self, mock_gemini_mapper_class, mock_logger):
        mock_mapper_instance = MagicMock()
        mock_gemini_mapper_class.return_value = mock_mapper_instance
        mock_mapper_instance.get_column_mapping.return_value = {'Company Name': 'company_name', 'Contact Email': 'email'}

        df = pd.DataFrame({
            'Company Name': ['Acme Corp', 'Globex'],
            'Contact Email': ['contact@acme.com', 'hello@globex.com'],
            'Unchanged': [1, 2]
        })

        result_df = _apply_ai_mapping(df)

        mock_mapper_instance.get_column_mapping.assert_called_once_with(['Company Name', 'Contact Email', 'Unchanged'])
        self.assertIn('company_name', result_df.columns)
        self.assertIn('email', result_df.columns)
        self.assertIn('Unchanged', result_df.columns)
        self.assertNotIn('Company Name', result_df.columns)
        self.assertNotIn('Contact Email', result_df.columns)
        mock_logger.info.assert_called_once_with("AI suggested mapping: %s", {'Company Name': 'company_name', 'Contact Email': 'email'})

    @patch('src.processors.ai_mapper.GeminiMapper')
    def test_apply_ai_mapping_no_mapping(self, mock_gemini_mapper_class):
        mock_mapper_instance = MagicMock()
        mock_gemini_mapper_class.return_value = mock_mapper_instance
        mock_mapper_instance.get_column_mapping.return_value = {}

        df = pd.DataFrame({
            'company_name': ['Acme Corp', 'Globex'],
            'email': ['contact@acme.com', 'hello@globex.com']
        })

        result_df = _apply_ai_mapping(df)

        mock_mapper_instance.get_column_mapping.assert_called_once_with(['company_name', 'email'])
        self.assertListEqual(list(result_df.columns), ['company_name', 'email'])

if __name__ == '__main__':
    unittest.main()
