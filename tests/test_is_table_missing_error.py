import unittest
import sys
import os

# Add the project root to sys.path to resolve backend imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.main import _is_table_missing_error

class TestIsTableMissingError(unittest.TestCase):
    def test_pgrst205_in_exception(self):
        """Test that an exception containing 'PGRST205' returns True."""
        e = Exception("Database error PGRST205: relation 'campaigns' does not exist")
        self.assertTrue(_is_table_missing_error(e))

    def test_pgrst205_not_in_exception(self):
        """Test that an exception without 'PGRST205' returns False."""
        e = Exception("Database error: Some other issue occurred")
        self.assertFalse(_is_table_missing_error(e))

if __name__ == '__main__':
    unittest.main()
