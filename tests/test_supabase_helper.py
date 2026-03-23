import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Mock external dependencies
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['supabase'] = MagicMock()
sys.modules['dotenv'] = MagicMock()
sys.modules['pandas'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['bs4'] = MagicMock()
sys.modules['fake_useragent'] = MagicMock()

sys.path.append(os.path.abspath(os.curdir))

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelper(unittest.TestCase):
    def setUp(self):
        # Prevent SupabaseHelper from complaining about missing env vars
        with patch.dict(os.environ, {"SUPABASE_URL": "http://fake.url", "SUPABASE_ANON_KEY": "fake_key"}):
            with patch('src.utils.supabase_helper.create_client') as mock_create_client:
                self.helper = SupabaseHelper()
                self.helper.client = MagicMock()

    def test_auto_migrate_sql_injection(self):
        # Test that invalid columns are skipped
        missing_columns = [
            "valid_column",
            "invalid_column_123",
            "123invalid",
            "invalid column",
            "invalid;DROP TABLE leads;",
            "in'valid"
        ]

        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = None
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        self.assertTrue(result)
        self.helper.client.rpc.assert_called_once()
        args, kwargs = self.helper.client.rpc.call_args
        self.assertEqual(args[0], "exec_sql")

        sql = args[1]["query"]
        self.assertIn("valid_column", sql)
        self.assertIn("invalid_column_123", sql)
        self.assertNotIn("123invalid", sql)
        self.assertNotIn("invalid column", sql)
        self.assertNotIn("DROP TABLE leads;", sql)
        self.assertNotIn("in'valid", sql)

        self.assertEqual(
            sql,
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS valid_column TEXT, ADD COLUMN IF NOT EXISTS invalid_column_123 TEXT;"
        )

    def test_auto_migrate_no_valid_columns(self):
        missing_columns = ["123invalid", "invalid column", "invalid;DROP TABLE leads;"]

        mock_rpc = MagicMock()
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        self.assertFalse(result)
        self.helper.client.rpc.assert_not_called()

    def test_check_schema_no_client(self):
        """Test check_schema when client is None."""
        self.helper.client = None
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_initial_fetch_error(self):
        """Test check_schema when the initial '*' select throws an exception."""
        self.helper.client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("General DB error")
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_all_exist(self):
        """Test check_schema when all columns exist."""
        # By default, MagicMock won't raise any exception on method calls, so all execute() succeed
        self.assertEqual(self.helper.check_schema(), [])

    def test_check_schema_some_missing(self):
        """Test check_schema when some columns are missing and Supabase throws exceptions."""
        missing_cols_to_simulate = ["seo_score", "facebook"]

        def mock_select(col):
            chain_mock = MagicMock()
            if col in missing_cols_to_simulate:
                chain_mock.limit.return_value.execute.side_effect = Exception(f'column "{col}" does not exist')
            elif col == "tiktok":
                chain_mock.limit.return_value.execute.side_effect = Exception("Some other random exception")
            else:
                chain_mock.limit.return_value.execute.return_value = MagicMock(data=[])
            return chain_mock

        self.helper.client.table.return_value.select.side_effect = mock_select

        missing = self.helper.check_schema()

        # It should only catch the missing column ones
        self.assertIn("seo_score", missing)
        self.assertIn("facebook", missing)
        self.assertNotIn("tiktok", missing)
        self.assertEqual(len(missing), 2)

class TestSupabaseHelperUpsert(unittest.TestCase):
    def setUp(self):
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
