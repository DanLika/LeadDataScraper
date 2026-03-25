import unittest
from unittest.mock import patch, MagicMock
import os

class TestSupabaseHelperUpdateAudit(unittest.TestCase):
    @patch.dict(os.environ, {"SUPABASE_URL": "http://mock-url.com", "SUPABASE_ANON_KEY": "mock-key"})
    @patch("src.utils.supabase_helper.create_client")
    def setUp(self, mock_create_client):
        from src.utils.supabase_helper import SupabaseHelper
        self.mock_client = MagicMock()
        mock_create_client.return_value = self.mock_client
        self.helper = SupabaseHelper()

    def test_no_client(self):
        """Test when client is not initialized (e.g., missing env vars)."""
        from src.utils.supabase_helper import SupabaseHelper
        with patch.dict(os.environ, {}, clear=True):
            helper_no_client = SupabaseHelper()
            self.assertIsNone(helper_no_client.client)
            result = helper_no_client.update_audit("test_key", {"score": "90"})
            self.assertIsNone(result)

    def test_happy_path_full_data(self):
        """Test with all fields present and correctly formatted."""
        audit_data = {
            "emails": ["test@example.com", "other@example.com"],
            "score": "85.5",
            "high_risk_flag": True,
            "other_field": "some_value"
        }

        # Setup mock chain
        mock_update = self.mock_client.table.return_value.update
        mock_eq = mock_update.return_value.eq
        mock_execute = mock_eq.return_value.execute
        mock_execute.return_value = {"status": "success"}

        result = self.helper.update_audit("test_key_1", audit_data)

        # Assertions
        self.mock_client.table.assert_called_with("leads")

        expected_update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data,
            "email": "test@example.com",
            "seo_score": 85.5,
            "high_risk_flag": True
        }
        mock_update.assert_called_with(expected_update_data)
        mock_eq.assert_called_with("unique_key", "test_key_1")
        mock_execute.assert_called_once()
        self.assertEqual(result, {"status": "success"})

    def test_empty_emails_array(self):
        """Test with an empty emails array."""
        audit_data = {"emails": []}

        mock_update = self.mock_client.table.return_value.update

        self.helper.update_audit("test_key_2", audit_data)

        expected_update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data
        }
        mock_update.assert_called_with(expected_update_data)

    def test_invalid_score(self):
        """Test with a score that cannot be cast to float."""
        audit_data = {"score": "N/A"}

        mock_update = self.mock_client.table.return_value.update

        self.helper.update_audit("test_key_3", audit_data)

        expected_update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data,
            "seo_score": 0
        }
        mock_update.assert_called_with(expected_update_data)

    def test_type_error_score(self):
        """Test with a score that causes TypeError (e.g. None)."""
        audit_data = {"score": None}

        mock_update = self.mock_client.table.return_value.update

        self.helper.update_audit("test_key_4", audit_data)

        expected_update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data,
            "seo_score": 0
        }
        mock_update.assert_called_with(expected_update_data)

    def test_high_risk_flag_casting(self):
        """Test high_risk_flag casting to boolean."""
        audit_data = {"high_risk_flag": "True"} # string value

        mock_update = self.mock_client.table.return_value.update

        self.helper.update_audit("test_key_5", audit_data)

        expected_update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data,
            "high_risk_flag": True
        }
        mock_update.assert_called_with(expected_update_data)

    def test_execute_exception(self):
        """Test exception handling during database execute."""
        audit_data = {"score": "50"}

        mock_execute = self.mock_client.table.return_value.update.return_value.eq.return_value.execute
        mock_execute.side_effect = Exception("Database error")

        # Mock logger to avoid noisy output during tests if desired, but we can just let it run
        with patch("src.utils.supabase_helper.logger.error") as mock_logger:
            result = self.helper.update_audit("test_key_6", audit_data)

            self.assertIsNone(result)
            mock_logger.assert_called_once()
            self.assertTrue("Error updating audit" in mock_logger.call_args[0][0])

if __name__ == '__main__':
    unittest.main()
