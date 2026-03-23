import unittest
from src.utils.json_helper import extract_json_from_response

class TestJsonHelper(unittest.TestCase):
    def test_extract_json_empty_response(self):
        """Test that empty strings return None as expected."""
        self.assertIsNone(extract_json_from_response(''))
        self.assertIsNone(extract_json_from_response(None))

if __name__ == '__main__':
    unittest.main()
