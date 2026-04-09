import unittest
from unittest.mock import patch, MagicMock
import sys

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

mock_np = MagicMock()
mock_np.nan = "nan"
sys.modules['numpy'] = mock_np

# Mock logger
mock_logging_config = MagicMock()
sys.modules['src.utils.logging_config'] = mock_logging_config

class TestCSVHelperHealth(unittest.TestCase):

    def setUp(self):
        # Reset mocks before each test
        mock_pd.reset_mock()
        mock_pd.read_csv.side_effect = None
        mock_pd.read_csv.return_value = MagicMock()

    def test_load_csv_file_not_found(self):
        # Setup: pandas.read_csv raises FileNotFoundError
        mock_pd.read_csv.side_effect = FileNotFoundError("No such file or directory")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("nonexistent.csv", "TestDB")

        # Verify it returns a DataFrame (mocked)
        mock_pd.DataFrame.assert_called()
        mock_pd.read_csv.assert_called_with("nonexistent.csv", dtype=str)

    def test_load_csv_empty_data_error(self):
        # Setup: pandas.read_csv raises EmptyDataError
        mock_pd.read_csv.side_effect = MockEmptyDataError("No columns to parse from file")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("headers_only.csv", "TestDB")

        mock_pd.DataFrame.assert_called()
        mock_pd.read_csv.assert_called_with("headers_only.csv", dtype=str)

    def test_load_csv_parser_error(self):
        # Setup: pandas.read_csv raises ParserError
        mock_pd.read_csv.side_effect = MockParserError("Error tokenizing data")

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("malformed.csv", "TestDB")

        mock_pd.DataFrame.assert_called()
        mock_pd.read_csv.assert_called_with("malformed.csv", dtype=str)

    def test_load_csv_success(self):
        # Setup: successful load
        mock_df = MagicMock()
        mock_df.columns = []
        mock_pd.read_csv.return_value = mock_df

        from src.utils.csv_helper import load_csv_with_unique_key
        df = load_csv_with_unique_key("valid.csv", "TestDB")

        self.assertEqual(df, mock_df)
        mock_pd.read_csv.assert_called_with("valid.csv", dtype=str)

if __name__ == '__main__':
    unittest.main()
