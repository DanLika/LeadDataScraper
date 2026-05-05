import unittest
from unittest.mock import MagicMock
from fastapi import UploadFile
import json

# Since all dependencies are installed, we can just import it directly
# without global sys.modules hacks.
from backend.main import validate_csv_upload

class TestCSVUploadValidation(unittest.TestCase):
    def test_valid_csv(self):
        # Create a mock valid CSV file
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "test_leads.csv"
        mock_file.content_type = "text/csv"

        contents = b"header1,header2\nvalue1,value2"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNone(result)

    def test_invalid_extension(self):
        # Create a mock invalid file (e.g., .txt)
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "test_leads.txt"
        mock_file.content_type = "text/plain"

        contents = b"some text"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body)
        self.assertEqual(body, {"error": "Only CSV files are allowed."})

    def test_invalid_content_type(self):
        # Mock file with valid extension but invalid content type
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "test_leads.csv"
        mock_file.content_type = "application/json"

        contents = b'{"key": "value"}'

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body)
        self.assertIn("Invalid content type", body["error"])

    def test_file_too_large(self):
        # Mock file with valid type but too large
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "large_leads.csv"
        mock_file.content_type = "text/csv"

        # 50MB + 1 byte
        contents = b"0" * (50 * 1024 * 1024 + 1)

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body)
        self.assertIn("File too large", body["error"])

    def test_missing_filename(self):
        # Mock file missing filename
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = None
        mock_file.content_type = "text/csv"

        contents = b"data"

        result = validate_csv_upload(mock_file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body)
        self.assertEqual(body, {"error": "Only CSV files are allowed."})

if __name__ == '__main__':
    unittest.main()
