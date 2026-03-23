import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock external dependencies before imports
sys.modules['supabase'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper


class TestSupabaseHelper(unittest.TestCase):
    @patch('src.utils.supabase_helper.create_client')
    @patch('src.utils.supabase_helper.os.environ.get')
    def test_delete_all_jobs_success(self, mock_env_get, mock_create_client):
        # Setup mocks
        mock_env_get.side_effect = lambda k: "dummy" if k in ["SUPABASE_URL", "SUPABASE_ANON_KEY"] else None

        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        # Setup the chain: table().delete().neq().execute()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table

        mock_delete = MagicMock()
        mock_table.delete.return_value = mock_delete

        mock_neq = MagicMock()
        mock_delete.neq.return_value = mock_neq

        expected_result = MagicMock()
        mock_neq.execute.return_value = expected_result

        # Execute
        helper = SupabaseHelper()
        result = helper.delete_all_jobs()

        # Assert
        self.assertEqual(result, expected_result)
        mock_client.table.assert_called_once_with("orchestration_jobs")
        mock_table.delete.assert_called_once()
        mock_delete.neq.assert_called_once_with("id", "null")
        mock_neq.execute.assert_called_once()

    @patch('src.utils.supabase_helper.os.environ.get')
    def test_delete_all_jobs_client_none(self, mock_env_get):
        # Setup mocks to return None for env vars, which makes self.client = None
        mock_env_get.return_value = None

        # Execute
        helper = SupabaseHelper()

        # Ensure client is None
        self.assertIsNone(helper.client)

        result = helper.delete_all_jobs()

        # Assert
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
