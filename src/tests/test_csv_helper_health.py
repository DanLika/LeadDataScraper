import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Ensure src is in path
sys.path.append(os.path.abspath(os.curdir))

# Define dummy exception classes to use for mocking pandas exceptions
class MockEmptyDataError(Exception):
    pass

class MockParserError(Exception):
    pass

class TestCSVHelperHealth(unittest.TestCase):
    def setUp(self):
        # Patch pandas, numpy, and logging inside src.utils.csv_helper to avoid affecting other tests.
        self.pd_patcher = patch('src.utils.csv_helper.pd')
        self.np_patcher = patch('src.utils.csv_helper.np')
        self.logger_patcher = patch('src.utils.csv_helper.logger')

        self.mock_pd = self.pd_patcher.start()
        self.mock_np = self.np_patcher.start()
        self.mock_logger = self.logger_patcher.start()

        # Set up custom errors on mock_pd
        self.mock_pd.errors.EmptyDataError = MockEmptyDataError
        self.mock_pd.errors.ParserError = MockParserError
        self.mock_np.nan = "nan"

        # Initialize common mock return values
        self.mock_pd.read_csv.side_effect = None
        self.mock_pd.read_csv.return_value = MagicMock()

    def tearDown(self):
        self.pd_patcher.stop()
        self.np_patcher.stop()
        self.logger_patcher.stop()

    def test_load_csv_file_not_found(self):
        # Setup: pandas.read_csv raises FileNotFoundError
        self.mock_pd.read_csv.side_effect = FileNotFoundError("No such file or directory")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("nonexistent.csv", "TestDB")

        # Verify it returns a DataFrame (mocked)
        self.mock_pd.DataFrame.assert_called()
        self.mock_pd.read_csv.assert_called_with("nonexistent.csv", dtype=str)

    def test_load_csv_empty_data_error(self):
        # Setup: pandas.read_csv raises EmptyDataError
        self.mock_pd.read_csv.side_effect = MockEmptyDataError("No columns to parse from file")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("headers_only.csv", "TestDB")

        self.mock_pd.DataFrame.assert_called()
        self.mock_pd.read_csv.assert_called_with("headers_only.csv", dtype=str)

    def test_load_csv_parser_error(self):
        # Setup: pandas.read_csv raises ParserError
        self.mock_pd.read_csv.side_effect = MockParserError("Error tokenizing data")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("malformed.csv", "TestDB")

        self.mock_pd.DataFrame.assert_called()
        self.mock_pd.read_csv.assert_called_with("malformed.csv", dtype=str)

    def test_load_csv_success(self):
        # Setup: successful load
        mock_df = MagicMock()
        mock_df.columns = []
        self.mock_pd.read_csv.return_value = mock_df

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("valid.csv", "TestDB")

        self.assertEqual(df, mock_df)
        self.mock_pd.read_csv.assert_called_with("valid.csv", dtype=str)

if __name__ == '__main__':
    unittest.main()
