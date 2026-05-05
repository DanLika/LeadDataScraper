import unittest
import os
import sys
import io
import json
from fastapi import UploadFile

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

# Setup mock environment for Supabase and Gemini
os.environ["API_SECRET_KEY"] = "dummy"

# Import from backend.main
from backend.main import validate_csv_upload

class TestValidateCSVUpload(unittest.TestCase):
    def test_file_too_large(self):
        # Create a mock file and contents larger than 50MB
        file_mock = UploadFile(file=io.BytesIO(b""), filename="test.csv", headers={"content-type": "text/csv"})

        # 50 MB + 1 byte
        large_contents = b"a" * (50 * 1024 * 1024 + 1)

        response = validate_csv_upload(file_mock, large_contents)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)

        # Extract body message
        body = json.loads(response.body.decode())
        self.assertTrue("File too large" in body["error"])

    def test_not_a_csv_extension(self):
        # File doesn't have .csv extension
        file_mock = UploadFile(file=io.BytesIO(b""), filename="test.txt", headers={"content-type": "text/csv"})
        contents = b"test,data\n1,2"

        response = validate_csv_upload(file_mock, contents)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.body.decode())
        self.assertEqual(body["error"], "Only CSV files are allowed.")

    def test_no_filename(self):
        # File has no filename
        file_mock = UploadFile(file=io.BytesIO(b""), filename="", headers={"content-type": "text/csv"})
        contents = b"test,data\n1,2"

        response = validate_csv_upload(file_mock, contents)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.body.decode())
        self.assertEqual(body["error"], "Only CSV files are allowed.")

    def test_invalid_content_type(self):
        # File has invalid content type
        file_mock = UploadFile(file=io.BytesIO(b""), filename="test.csv", headers={"content-type": "application/json"})
        contents = b"test,data\n1,2"

        response = validate_csv_upload(file_mock, contents)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)
        body = json.loads(response.body.decode())
        self.assertTrue("Invalid content type" in body["error"])

    def test_valid_csv(self):
        # Valid csv
        file_mock = UploadFile(file=io.BytesIO(b""), filename="test.csv", headers={"content-type": "text/csv"})
        contents = b"test,data\n1,2"

        response = validate_csv_upload(file_mock, contents)

        # Should return None when valid
        self.assertIsNone(response)

if __name__ == '__main__':
    unittest.main()
