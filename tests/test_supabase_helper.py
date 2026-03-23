import unittest
from unittest.mock import MagicMock, patch, sys

# Mock external dependencies before imports
sys.modules['supabase'] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper

class TestSupabaseHelper(unittest.TestCase):

    @patch('src.utils.supabase_helper.create_client')
    @patch('src.utils.supabase_helper.os.environ.get')
    def setUp(self, mock_env_get, mock_create_client):
        # Set up a fake environment
        mock_env_get.side_effect = lambda k: "http://fake_url" if k == "SUPABASE_URL" else "fake_key"
        self.mock_client = MagicMock()
        mock_create_client.return_value = self.mock_client
        self.helper = SupabaseHelper()

    def test_delete_all_leads_success(self):
        """Verify that delete_all_leads calls the correct query chain."""
        # Setup mock return values for the query chain: table("leads").delete().neq("unique_key", "null").execute()
        mock_table = MagicMock()
        mock_delete = MagicMock()
        mock_neq = MagicMock()
        mock_execute = MagicMock()

        self.mock_client.table.return_value = mock_table
        mock_table.delete.return_value = mock_delete
        mock_delete.neq.return_value = mock_neq
        mock_neq.execute.return_value = mock_execute

        mock_execute.data = [{"unique_key": "123"}]

        result = self.helper.delete_all_leads()

        # Assert correct chain is called
        self.mock_client.table.assert_called_once_with("leads")
        mock_table.delete.assert_called_once()
        mock_delete.neq.assert_called_once_with("unique_key", "null")
        mock_neq.execute.assert_called_once()

        # Verify the returned object is the execute() return value
        self.assertEqual(result, mock_execute)

    def test_delete_all_leads_no_client(self):
        """Verify that delete_all_leads returns None if client is None."""
        self.helper.client = None
        result = self.helper.delete_all_leads()
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
