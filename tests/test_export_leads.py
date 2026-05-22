import unittest
import sys
from unittest.mock import MagicMock

# Mock supabase to avoid ModuleNotFoundError since it's not needed for is_high_priority
if 'supabase' not in sys.modules:
    sys.modules['supabase'] = MagicMock()

from src.scripts.export_leads import is_high_priority

class TestExportLeads(unittest.TestCase):
    def test_is_high_priority_no_audit(self):
        row = {}
        self.assertFalse(is_high_priority(row))

    def test_is_high_priority_missing_score(self):
        row = {'audit_results': {}}
        self.assertFalse(is_high_priority(row))

    def test_is_high_priority_score_above_50(self):
        row = {'audit_results': {'score': 80}}
        self.assertFalse(is_high_priority(row))

    def test_is_high_priority_score_exactly_50(self):
        row = {'audit_results': {'score': 50}}
        self.assertFalse(is_high_priority(row))

    def test_is_high_priority_score_below_50(self):
        row = {'audit_results': {'score': 49}}
        self.assertTrue(is_high_priority(row))

    def test_is_high_priority_score_invalid_string(self):
        row = {'audit_results': {'score': 'invalid'}}
        self.assertFalse(is_high_priority(row))

    def test_is_high_priority_score_valid_string(self):
        row = {'audit_results': {'score': '40.5'}}
        self.assertTrue(is_high_priority(row))

    def test_is_high_priority_score_null(self):
        row = {'audit_results': {'score': None}}
        self.assertFalse(is_high_priority(row))

if __name__ == '__main__':
    unittest.main()
