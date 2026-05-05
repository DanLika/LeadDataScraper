import unittest
import sys
import importlib
from unittest.mock import MagicMock

class TestExtractNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mock dependencies before importing the module under test
        cls.original_pandas = sys.modules.get('pandas')
        cls.original_supabase = sys.modules.get('src.utils.supabase_helper')
        sys.modules['pandas'] = MagicMock()
        sys.modules['src.utils.supabase_helper'] = MagicMock()

        # Import the module now that dependencies are mocked
        from src.scripts import export_leads
        importlib.reload(export_leads)
        cls.extract_names = staticmethod(export_leads.extract_names)

    @classmethod
    def tearDownClass(cls):
        # Restore original dependencies
        if cls.original_pandas:
            sys.modules['pandas'] = cls.original_pandas
        else:
            del sys.modules['pandas']

        if cls.original_supabase:
            sys.modules['src.utils.supabase_helper'] = cls.original_supabase
        else:
            del sys.modules['src.utils.supabase_helper']
    def test_empty_dict(self):
        self.assertEqual(self.extract_names({}), ("Business", "Owner"))

    def test_none_value(self):
        self.assertEqual(self.extract_names({'leadership_team': None}), ("Business", "Owner"))

    def test_empty_string(self):
        self.assertEqual(self.extract_names({'leadership_team': ''}), ("Business", "Owner"))

    def test_unknown_string(self):
        self.assertEqual(self.extract_names({'leadership_team': 'Unknown'}), ("Business", "Owner"))

    def test_whitespace_string(self):
        self.assertEqual(self.extract_names({'leadership_team': '   '}), ("Business", "Owner"))

    def test_single_comma(self):
        self.assertEqual(self.extract_names({'leadership_team': ','}), ("Business", "Owner"))

    def test_single_name(self):
        self.assertEqual(self.extract_names({'leadership_team': 'John'}), ("John", ""))

    def test_two_names_space(self):
        self.assertEqual(self.extract_names({'leadership_team': 'John Doe'}), ("John", "Doe"))

    def test_two_names_comma(self):
        self.assertEqual(self.extract_names({'leadership_team': 'John,Doe'}), ("John", "Doe"))

    def test_multiple_names(self):
        self.assertEqual(self.extract_names({'leadership_team': 'John, Doe Smith'}), ("John", "Doe Smith"))

    def test_names_with_extra_spaces(self):
        self.assertEqual(self.extract_names({'leadership_team': '  John   Doe  '}), ("John", "Doe"))

if __name__ == "__main__":
    unittest.main()
