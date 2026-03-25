import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

# Mock the entire supabase and dotenv module before importing SupabaseHelper
mock_supabase = MagicMock()
sys.modules["supabase"] = mock_supabase
sys.modules["supabase.client"] = mock_supabase
sys.modules["dotenv"] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelperUpsert(unittest.TestCase):
    def setUp(self):
        # Mock environment variables to allow SupabaseHelper initialization
        with patch.dict(os.environ, {"SUPABASE_URL": "http://mock-url.com", "SUPABASE_ANON_KEY": "mock-key"}):
            self.helper = SupabaseHelper()
        self.helper.client = MagicMock()

    @patch("src.utils.supabase_helper.logger")
    def test_upsert_leads_schema_mismatch(self, mock_logger):
        """Test that schema mismatch exceptions are caught and logged appropriately."""
        leads = [{"unique_key": "test1", "invalid_col": "value"}]

        # Configure mock to raise exception with "column" and "does not exist"
        schema_exception = Exception('column "invalid_col" of relation "leads" does not exist')
        self.helper.client.table.return_value.upsert.return_value.execute.side_effect = schema_exception

        result = self.helper.upsert_leads(leads)

        # Assert result is None
        self.assertIsNone(result)

        # Assert logger.error was called with DATABASE SCHEMA MISMATCH
        mock_logger.error.assert_any_call("DATABASE SCHEMA MISMATCH: %s", schema_exception)

        # Assert logger.warning was called
        mock_logger.warning.assert_called_with("Please run the SQL migration script provided in the implementation plan.")

    @patch("src.utils.supabase_helper.logger")
    def test_upsert_leads_other_error(self, mock_logger):
        """Test that non-schema errors are logged using exc_info=True."""
        leads = [{"unique_key": "test1", "name": "value"}]

        # Configure mock to raise a different exception
        test_exception = Exception("Connection timeout")
        self.helper.client.table.return_value.upsert.return_value.execute.side_effect = test_exception

        result = self.helper.upsert_leads(leads)

        # Assert result is None
        self.assertIsNone(result)

        # Assert logger.error was called for general error
        mock_logger.error.assert_called_with("Error upserting leads: %s", test_exception, exc_info=True)

if __name__ == "__main__":
    unittest.main()
