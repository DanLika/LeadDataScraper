import sys
import os
sys.path.append(os.path.abspath(os.curdir))

import unittest
from unittest.mock import MagicMock
import json
from fastapi import UploadFile

from backend.main import validate_csv_upload

class TestValidateCsvUpload(unittest.TestCase):
    def test_valid_csv(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = "text/csv"
        contents = b"some,csv,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNone(result)

    def test_invalid_extension(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.txt"
        file.content_type = "text/csv"
        contents = b"some,txt,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body.decode('utf-8'))
        self.assertEqual(body["error"], "Only CSV files are allowed.")

    def test_missing_filename(self):
        file = MagicMock(spec=UploadFile)
        file.filename = None
        file.content_type = "text/csv"
        contents = b"some,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body.decode('utf-8'))
        self.assertEqual(body["error"], "Only CSV files are allowed.")

    def test_missing_content_type(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = None
        contents = b"some,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNone(result)

    def test_invalid_content_type(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = "application/json"
        contents = b'{"a": 1}'

        result = validate_csv_upload(file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body.decode('utf-8'))
        self.assertIn("Invalid content type", body["error"])

    def test_file_too_large(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = "text/csv"
        # 50MB + 1 byte
        contents = b"a" * ((50 * 1024 * 1024) + 1)

        result = validate_csv_upload(file, contents)
        self.assertIsNotNone(result)
        self.assertEqual(result.status_code, 400)
        body = json.loads(result.body.decode('utf-8'))
        self.assertIn("File too large", body["error"])

    def test_valid_excel_content_type(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = "application/vnd.ms-excel"
        contents = b"some,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNone(result)

    def test_valid_octet_stream_content_type(self):
        file = MagicMock(spec=UploadFile)
        file.filename = "test.csv"
        file.content_type = "application/octet-stream"
        contents = b"some,data"

        result = validate_csv_upload(file, contents)
        self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()
