import unittest
from unittest.mock import patch, MagicMock
import sys

# Define dummy exception classes to use for mocking pandas exceptions
class MockEmptyDataError(Exception):
    pass

class MockParserError(Exception):
    pass

class TestCSVHelperHealth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mock_pd = MagicMock()
        cls.mock_pd.errors.EmptyDataError = MockEmptyDataError
        cls.mock_pd.errors.ParserError = MockParserError
        cls.patcher_pd = patch('src.utils.csv_helper.pd', cls.mock_pd)
        cls.patcher_pd.start()

        cls.mock_np = MagicMock()
        cls.mock_np.nan = "nan"
        cls.patcher_np = patch('src.utils.csv_helper.np', cls.mock_np)
        cls.patcher_np.start()

        cls.mock_logging = MagicMock()
        cls.patcher_logging = patch('src.utils.csv_helper.logger', cls.mock_logging)
        cls.patcher_logging.start()

    @classmethod
    def tearDownClass(cls):
        cls.patcher_pd.stop()
        cls.patcher_np.stop()
        cls.patcher_logging.stop()

    def setUp(self):
        # Reset mocks before each test
        self.mock_pd.reset_mock()
        self.mock_pd.read_csv.side_effect = None
        self.mock_pd.read_csv.return_value = MagicMock()

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
