import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock external dependencies
sys.modules['playwright'] = MagicMock()
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

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelper(unittest.TestCase):
    def setUp(self):
        with patch('src.utils.supabase_helper.create_client') as mock_create_client:
            with patch('os.environ.get') as mock_env_get:
                mock_env_get.side_effect = lambda k: "dummy_val" if k in ["SUPABASE_URL", "SUPABASE_ANON_KEY"] else None
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

        # Setup mock for rpc
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = None
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        self.assertTrue(result)

        # Verify the generated SQL contains only valid columns
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
        # Test when all columns are invalid
        missing_columns = ["123invalid", "invalid column", "invalid;DROP TABLE leads;"]

        mock_rpc = MagicMock()
        self.helper.client.rpc.return_value = mock_rpc

        result = self.helper.auto_migrate(missing_columns)

        # Method should return False because no valid columns exist
        self.assertFalse(result)
        # rpc should not be called
        self.helper.client.rpc.assert_not_called()

if __name__ == '__main__':
    unittest.main()
