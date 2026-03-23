import unittest
from unittest.mock import MagicMock, patch
import os
import sys

sys.path.append(os.path.abspath(os.curdir))

# Provide mock for sys.modules so it doesn't fail on create_client validation
sys.modules["supabase"] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelper(unittest.TestCase):
    def setUp(self):
        # Prevent the actual client from being created by mocking the env vars
        with patch.dict(os.environ, {"SUPABASE_URL": "http://test-url", "SUPABASE_ANON_KEY": "test-key"}), \
             patch("src.utils.supabase_helper.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            self.helper = SupabaseHelper()

    def test_upsert_leads_no_client(self):
        """Test upsert_leads when self.client is None."""
        self.helper.client = None
        result = self.helper.upsert_leads([{"unique_key": "123"}])
        self.assertIsNone(result)

    def test_upsert_leads_success(self):
        """Test successful upsert of leads."""
        leads_data = [{"unique_key": "123", "name": "Test Lead"}]
        mock_execute = MagicMock(return_value="success_result")
        mock_upsert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(upsert=mock_upsert))
        self.helper.client.table = mock_table

        with patch("src.utils.supabase_helper.logger") as mock_logger:
            result = self.helper.upsert_leads(leads_data)

            self.assertEqual(result, "success_result")
            mock_table.assert_called_once_with("leads")
            mock_upsert.assert_called_once_with(leads_data)
            mock_execute.assert_called_once()
            mock_logger.info.assert_called_with("Successfully upserted %d leads to Supabase.", 1)

    def test_upsert_leads_exception_schema_mismatch(self):
        """Test upsert_leads handling schema mismatch (column does not exist)."""
        leads_data = [{"unique_key": "123", "name": "Test Lead"}]

        # Make execute() raise an Exception containing "column" and "does not exist"
        mock_execute = MagicMock(side_effect=Exception('column "missing_col" does not exist'))
        mock_upsert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(upsert=mock_upsert))
        self.helper.client.table = mock_table

        with patch("src.utils.supabase_helper.logger") as mock_logger:
            result = self.helper.upsert_leads(leads_data)

            self.assertIsNone(result)
            mock_logger.error.assert_called_once()
            self.assertIn("DATABASE SCHEMA MISMATCH:", mock_logger.error.call_args[0][0])
            mock_logger.warning.assert_called_with("Please run the SQL migration script provided in the implementation plan.")

    def test_upsert_leads_exception_general(self):
        """Test upsert_leads handling a general exception."""
        leads_data = [{"unique_key": "123", "name": "Test Lead"}]

        # Make execute() raise a general Exception
        mock_execute = MagicMock(side_effect=Exception('Connection timeout'))
        mock_upsert = MagicMock(return_value=MagicMock(execute=mock_execute))
        mock_table = MagicMock(return_value=MagicMock(upsert=mock_upsert))
        self.helper.client.table = mock_table

        with patch("src.utils.supabase_helper.logger") as mock_logger:
            result = self.helper.upsert_leads(leads_data)

            self.assertIsNone(result)
            mock_logger.error.assert_called_once()
            self.assertEqual("Error upserting leads: %s", mock_logger.error.call_args[0][0])
            self.assertEqual("Connection timeout", str(mock_logger.error.call_args[0][1]))

if __name__ == "__main__":
    unittest.main()
