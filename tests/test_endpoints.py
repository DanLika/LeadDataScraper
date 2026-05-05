import sys
import os
import unittest
import asyncio
from unittest.mock import patch, MagicMock

# Set up environment variables to allow import
os.environ["SUPABASE_URL"] = "http://fake.url"
os.environ["SUPABASE_ANON_KEY"] = "fake_key_1234567890_to_pass_validation_length_check_12345678901234567890"
os.environ["API_SECRET_KEY"] = "test-key-123"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"

from fastapi.testclient import TestClient

class TestListLeads(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # We patch environment variables that main.py needs at import time
        cls.env_patcher = patch.dict(os.environ, {
            "SUPABASE_URL": "http://fake.url",
            "SUPABASE_ANON_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRlc3QiLCJyb2xlIjoiYW5vbiIsImlhdCI6MTcwMDAwMDAwMCwiZXhwIjoxODAwMDAwMDAwfQ.invalid_signature", # Need a JWT-like string for supabase key validation
            "API_SECRET_KEY": "test-key-123",
            "ALLOWED_ORIGINS": "http://localhost:3000",
        })
        cls.env_patcher.start()

        # Try removing main if it's already imported to reload it cleanly
        if 'backend.main' in sys.modules:
            del sys.modules['backend.main']

    @classmethod
    def tearDownClass(cls):
        cls.env_patcher.stop()

    def setUp(self):
        # Prevent supabase client from actually trying to connect
        with patch('src.utils.supabase_helper.create_client') as mock_create_client:
            mock_create_client.return_value = MagicMock()

            # Import the real components
            from backend.main import app, db

            # Override the APIError reference in backend.main just in case it got cached differently
            # If postgrest is not available, we use our own mock
            try:
                from postgrest.exceptions import APIError
            except ImportError:
                class APIError(Exception):
                    pass
                sys.modules['postgrest'] = MagicMock()
                sys.modules['postgrest.exceptions'] = MagicMock()
                sys.modules['postgrest.exceptions'].APIError = APIError

            import backend.main
            backend.main.APIError = APIError
            self.APIError = APIError

            self.app = app
            self.client = TestClient(app)
            self.db = db

    def test_list_leads_success(self):
        """Test happy path for listing leads."""
        mock_response = MagicMock()
        mock_response.data = [{"unique_key": "123", "name": "Test Lead"}]

        with patch.object(self.db, 'client') as mock_client:
            mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = mock_response

            response = self.client.get("/leads", headers={"X-API-Key": "test-key-123"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"leads": [{"unique_key": "123", "name": "Test Lead"}]})

    def test_list_leads_db_not_connected(self):
        """Test list_leads when db.client is None."""
        # Store original client
        original_client = self.db.client
        self.db.client = None

        try:
            response = self.client.get("/leads", headers={"X-API-Key": "test-key-123"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"error": "Database not connected"})
        finally:
            # Restore
            self.db.client = original_client

    def test_list_leads_api_error(self):
        """Test list_leads when Supabase raises an APIError."""
        with patch.object(self.db, 'client') as mock_client:
            mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.side_effect = self.APIError(
                {"message": "Database error", "code": "500", "details": "", "hint": ""}
            )

            response = self.client.get("/leads", headers={"X-API-Key": "test-key-123"})

            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json(), {"error": "Failed to fetch leads from database"})

    def test_list_leads_unexpected_error(self):
        """Test list_leads when an unexpected exception occurs."""
        with patch.object(self.db, 'client') as mock_client:
            mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.side_effect = Exception("Unexpected")

            response = self.client.get("/leads", headers={"X-API-Key": "test-key-123"})

            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"error": "An unexpected error occurred while fetching leads"})

if __name__ == '__main__':
    unittest.main()
