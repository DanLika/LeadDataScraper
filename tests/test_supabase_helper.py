import unittest
from unittest.mock import MagicMock, patch
# Need to mock os.environ before SupabaseHelper __init__
import os

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelper(unittest.TestCase):
    def setUp(self):
        # Prevent SupabaseHelper from complaining about missing env vars
        with patch.dict(os.environ, {"SUPABASE_URL": "http://fake.url", "SUPABASE_ANON_KEY": "fake_key"}):
            with patch('src.utils.supabase_helper.create_client') as mock_create_client:
                self.helper = SupabaseHelper()
                self.helper.client = MagicMock()

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

if __name__ == '__main__':
    unittest.main()
