import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock external dependencies before importing src
sys.modules['supabase'] = MagicMock()

from src.utils.supabase_helper import SupabaseHelper


class TestSupabaseHelper(unittest.TestCase):

    @patch('src.utils.supabase_helper.create_client')
    @patch('src.utils.supabase_helper.os.environ.get')
    def test_get_pending_leads_no_client(self, mock_env_get, mock_create_client):
        """Test get_pending_leads when client initialization fails (e.g. missing env vars)."""
        # Simulate missing environment variables
        mock_env_get.return_value = None

        helper = SupabaseHelper()
        self.assertIsNone(helper.client)

        result = helper.get_pending_leads()

        self.assertEqual(result, [])
        mock_create_client.assert_not_called()

    @patch('src.utils.supabase_helper.create_client')
    @patch('src.utils.supabase_helper.os.environ.get')
    def test_get_pending_leads_with_client(self, mock_env_get, mock_create_client):
        """Test get_pending_leads when client is properly initialized."""
        # Setup dummy environment variables
        mock_env_get.side_effect = lambda k: "dummy_url" if k == "SUPABASE_URL" else "dummy_key"

        # Setup mock Supabase client and query chain
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        mock_table = mock_client.table.return_value
        mock_select = mock_table.select.return_value
        mock_eq = mock_select.eq.return_value
        mock_execute = mock_eq.execute

        # Set the mock return value for the execute call
        mock_response = MagicMock(data=[{"unique_key": "123", "audit_status": "Pending"}])
        mock_execute.return_value = mock_response

        # Initialize helper and verify client
        helper = SupabaseHelper()
        self.assertIsNotNone(helper.client)

        # Call the method
        result = helper.get_pending_leads()

        # Verify the chain of calls
        mock_client.table.assert_called_once_with("leads")
        mock_table.select.assert_called_once_with("*")
        mock_select.eq.assert_called_once_with("audit_status", "Pending")
        mock_execute.assert_called_once()

        # Verify result is correct
        self.assertEqual(result, mock_response)


if __name__ == "__main__":
    unittest.main()
