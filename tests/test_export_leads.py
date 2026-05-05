import unittest
import os
import sys

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

from unittest.mock import MagicMock

# Define dummy exception classes to use for mocking pandas exceptions
class MockEmptyDataError(Exception):
    pass

class MockParserError(Exception):
    pass

# We create a more isolated mock for pandas that includes our custom exceptions.
mock_pd = MagicMock()
mock_pd.errors.EmptyDataError = MockEmptyDataError
mock_pd.errors.ParserError = MockParserError
sys.modules['pandas'] = mock_pd

# Mock SupabaseHelper which is imported in export_leads.py
mock_supabase_helper = MagicMock()
sys.modules['src.utils.supabase_helper'] = mock_supabase_helper

from src.scripts.export_leads import extract_names

class TestExtractNames(unittest.TestCase):
    def test_missing_key(self):
        # Missing 'leadership_team' key
        row = {}
        self.assertEqual(extract_names(row), ("Business", "Owner"))

    def test_none_value(self):
        # 'leadership_team' is None
        row = {'leadership_team': None}
        self.assertEqual(extract_names(row), ("Business", "Owner"))

    def test_unknown_value(self):
        # 'leadership_team' is 'Unknown'
        row = {'leadership_team': 'Unknown'}
        self.assertEqual(extract_names(row), ("Business", "Owner"))

    def test_empty_string(self):
        # 'leadership_team' is empty string
        row = {'leadership_team': ''}
        self.assertEqual(extract_names(row), ("Business", "Owner"))

    def test_single_name(self):
        # Value has a single name
        row = {'leadership_team': 'Alice'}
        self.assertEqual(extract_names(row), ("Alice", ""))

    def test_two_names(self):
        # Value has two names
        row = {'leadership_team': 'Alice Bob'}
        self.assertEqual(extract_names(row), ("Alice", "Bob"))

    def test_multiple_names(self):
        # Value has multiple names
        row = {'leadership_team': 'Alice Bob Charlie'}
        self.assertEqual(extract_names(row), ("Alice", "Bob Charlie"))

    def test_comma_separated(self):
        # Value has commas
        row1 = {'leadership_team': 'Alice,Bob'}
        self.assertEqual(extract_names(row1), ("Alice", "Bob"))

        row2 = {'leadership_team': 'Alice, Bob, Charlie'}
        self.assertEqual(extract_names(row2), ("Alice", "Bob Charlie"))

if __name__ == '__main__':
    unittest.main()
