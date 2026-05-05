import unittest
from unittest.mock import MagicMock
import sys
import os

# Ensure backend directory is in the python path to import main.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock heavy modules before importing main
sys.modules['supabase'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
# Since we mock playwright, we also need to mock its submodules used
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()

# Mock environment variables so main.py doesn't crash on startup
os.environ['API_SECRET_KEY'] = 'test'
os.environ['SUPABASE_URL'] = 'http://test.com'
os.environ['SUPABASE_KEY'] = 'test'

from backend.main import validate_csv_upload

class TestValidateCSVUpload(unittest.TestCase):
    def test_valid_csv(self):
        mock_file = MagicMock()
        mock_file.filename = "leads.csv"
        mock_file.content_type = "text/csv"

        contents = b"test,data\n1,2"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNone(result)

    def test_invalid_extension(self):
        mock_file = MagicMock()
        mock_file.filename = "document.pdf"
        mock_file.content_type = "application/pdf"

        contents = b"test data"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        import json
        body = json.loads(result.body.decode()) if isinstance(result.body, bytes) else json.loads(result.body)
        self.assertEqual(body["error"], "Only CSV files are allowed.")

    def test_missing_filename(self):
        mock_file = MagicMock()
        mock_file.filename = None
        mock_file.content_type = "text/csv"

        contents = b"test data"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)

    def test_invalid_content_type(self):
        mock_file = MagicMock()
        mock_file.filename = "fake.csv"
        mock_file.content_type = "application/json"

        contents = b"{}"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        import json
        body = json.loads(result.body.decode()) if isinstance(result.body, bytes) else json.loads(result.body)
        self.assertIn("Invalid content type", body["error"])

    def test_file_too_large(self):
        mock_file = MagicMock()
        mock_file.filename = "large.csv"
        mock_file.content_type = "text/csv"

        max_size = 50 * 1024 * 1024
        class FakeBytes:
            def __len__(self):
                return max_size + 1

        contents = FakeBytes()

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        import json
        body = json.loads(result.body.decode()) if isinstance(result.body, bytes) else json.loads(result.body)
        self.assertIn("File too large", body["error"])

if __name__ == "__main__":
    unittest.main()
