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

        cls.mock_np = MagicMock()
        cls.mock_np.nan = "nan"

        cls.mock_logging_config = MagicMock()

        cls.original_modules = {
            'pandas': sys.modules.get('pandas'),
            'numpy': sys.modules.get('numpy'),
            'src.utils.logging_config': sys.modules.get('src.utils.logging_config'),
        }

        sys.modules['pandas'] = cls.mock_pd
        sys.modules['numpy'] = cls.mock_np
        sys.modules['src.utils.logging_config'] = cls.mock_logging_config

        if 'src.utils.csv_helper' in sys.modules:
            del sys.modules['src.utils.csv_helper']
        if 'src.core.data_manager' in sys.modules:
            del sys.modules['src.core.data_manager']

    @classmethod
    def tearDownClass(cls):
        for mod, val in cls.original_modules.items():
            if val is None:
                if mod in sys.modules:
                    del sys.modules[mod]
            else:
                sys.modules[mod] = val
        if 'src.utils.csv_helper' in sys.modules:
            del sys.modules['src.utils.csv_helper']
        if 'src.core.data_manager' in sys.modules:
            del sys.modules['src.core.data_manager']

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

    def test_merge_and_deduplicate_error(self):
        # Setup: pandas.concat raises an Exception
        self.mock_pd.concat.side_effect = Exception("Mocked concatenation error")

        try:
            from src.core.data_manager import merge_and_deduplicate
        except ImportError:
            from src.utils.csv_helper import merge_and_deduplicate

        df = merge_and_deduplicate([MagicMock(), MagicMock()])

        self.mock_pd.DataFrame.assert_called()
        self.assertEqual(df, self.mock_pd.DataFrame.return_value)

if __name__ == '__main__':
    unittest.main()
