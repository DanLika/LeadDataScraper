import unittest
from unittest.mock import patch
import sys
import importlib

# Workaround: Restore real pandas and numpy if they were mocked by other tests (test pollution)
for mod in ['numpy', 'pandas']:
    if mod in sys.modules and type(sys.modules[mod]).__name__ == 'MagicMock':
        del sys.modules[mod]

real_np = importlib.import_module('numpy')
real_pd = importlib.import_module('pandas')

# Force reload csv_helper to bind the real pandas and numpy
if 'src.utils.csv_helper' in sys.modules:
    importlib.reload(sys.modules['src.utils.csv_helper'])

from src.utils.csv_helper import load_csv_with_unique_key

class TestCSVHelperHealth(unittest.TestCase):

    def setUp(self):
        # Ensure we always use real pandas in our tests despite global mocks
        self.patcher_pd = patch('src.utils.csv_helper.pd', real_pd)
        self.patcher_np = patch('src.utils.csv_helper.np', real_np)
        self.patcher_pd.start()
        self.patcher_np.start()

    def tearDown(self):
        self.patcher_pd.stop()
        self.patcher_np.stop()

    @patch('src.utils.csv_helper.pd.read_csv')
    def test_load_csv_file_not_found(self, mock_read_csv):
        mock_read_csv.side_effect = FileNotFoundError("No such file or directory")

        df = load_csv_with_unique_key("nonexistent.csv", "TestDB")

        mock_read_csv.assert_called_with("nonexistent.csv", dtype=str)
        self.assertTrue(df.empty)
        essential_cols = ['Name', 'Website', 'email', 'unique_key']
        for col in essential_cols:
            self.assertIn(col, df.columns)

    @patch('src.utils.csv_helper.pd.read_csv')
    def test_load_csv_empty_data_error(self, mock_read_csv):
        mock_read_csv.side_effect = real_pd.errors.EmptyDataError("No columns to parse from file")

        df = load_csv_with_unique_key("headers_only.csv", "TestDB")

        mock_read_csv.assert_called_with("headers_only.csv", dtype=str)
        self.assertTrue(df.empty)

    @patch('src.utils.csv_helper.pd.read_csv')
    def test_load_csv_parser_error(self, mock_read_csv):
        mock_read_csv.side_effect = real_pd.errors.ParserError("Error tokenizing data")

        df = load_csv_with_unique_key("malformed.csv", "TestDB")

        mock_read_csv.assert_called_with("malformed.csv", dtype=str)
        self.assertTrue(df.empty)

    @patch('src.utils.csv_helper.pd.read_csv')
    def test_load_csv_success(self, mock_read_csv):
        mock_df = real_pd.DataFrame({'Name': ['Test'], 'Website': ['test.com'], 'email': ['test@test.com'], 'unique_key': ['test_1']})
        mock_read_csv.return_value = mock_df

        df = load_csv_with_unique_key("valid.csv", "TestDB")

        self.assertEqual(len(df), 1)
        mock_read_csv.assert_called_with("valid.csv", dtype=str)

if __name__ == '__main__':
    unittest.main()
