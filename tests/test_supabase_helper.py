import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

class TestSupabaseHelper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mock external dependencies before imports
        cls.mock_supabase = MagicMock()
        cls.patcher_supabase = patch.dict('sys.modules', {
            'supabase': cls.mock_supabase,
            'supabase.client': cls.mock_supabase,
            'dotenv': MagicMock()
        })
        cls.patcher_supabase.start()

        # Import the module under test globally inside setUpClass
        global SupabaseHelper
        from src.utils.supabase_helper import SupabaseHelper

    @classmethod
    def tearDownClass(cls):
        cls.patcher_supabase.stop()

    def setUp(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "http://mock-url.com", "SUPABASE_ANON_KEY": "mock-key"}):
            self.helper = SupabaseHelper()
        self.helper.client = MagicMock()

    def test_auto_migrate_no_client(self):
        self.helper.client = None
        result = self.helper.auto_migrate(["new_column"])
        self.assertFalse(result)

    def test_auto_migrate_no_missing_columns(self):
        result = self.helper.auto_migrate([])
        self.assertFalse(result)
        self.helper.client.rpc.assert_not_called()

    def test_auto_migrate_success(self):
        missing_columns = ["col1", "col2"]
        expected_sql = "ALTER TABLE leads ADD COLUMN IF NOT EXISTS col1 TEXT, ADD COLUMN IF NOT EXISTS col2 TEXT;"

        self.helper.client.rpc.return_value.execute.return_value = MagicMock()

        result = self.helper.auto_migrate(missing_columns)

        self.assertTrue(result)
        self.helper.client.rpc.assert_called_once_with("exec_sql", {"query": expected_sql})
        self.helper.client.rpc.return_value.execute.assert_called_once()

    def test_auto_migrate_exception(self):
        missing_columns = ["col1"]
        self.helper.client.rpc.side_effect = Exception("RPC failed")

        result = self.helper.auto_migrate(missing_columns)

        self.assertFalse(result)
        self.helper.client.rpc.assert_called_once()

if __name__ == '__main__':
    unittest.main()
