import unittest
from unittest.mock import patch, MagicMock
import sys

import pandas as pd
from pandas.errors import EmptyDataError, ParserError
from src.utils.csv_helper import load_csv_with_unique_key

class TestCSVHelperHealth(unittest.TestCase):

    @patch('src.utils.csv_helper.pd.read_csv')
    @patch('src.utils.csv_helper.pd.DataFrame')
    def test_load_csv_file_not_found(self, mock_df_class, mock_read_csv):
        mock_read_csv.side_effect = FileNotFoundError("No such file or directory")
        df = load_csv_with_unique_key("nonexistent.csv", "TestDB")
        mock_df_class.assert_called()
        mock_read_csv.assert_called_with("nonexistent.csv", dtype=str)

    @patch('src.utils.csv_helper.pd.read_csv')
    @patch('src.utils.csv_helper.pd.DataFrame')
    def test_load_csv_empty_data_error(self, mock_df_class, mock_read_csv):
        mock_read_csv.side_effect = EmptyDataError("No columns to parse from file")
        df = load_csv_with_unique_key("headers_only.csv", "TestDB")
        mock_df_class.assert_called()
        mock_read_csv.assert_called_with("headers_only.csv", dtype=str)

    @patch('src.utils.csv_helper.pd.read_csv')
    @patch('src.utils.csv_helper.pd.DataFrame')
    def test_load_csv_parser_error(self, mock_df_class, mock_read_csv):
        mock_read_csv.side_effect = ParserError("Error tokenizing data")
        df = load_csv_with_unique_key("malformed.csv", "TestDB")
        mock_df_class.assert_called()
        mock_read_csv.assert_called_with("malformed.csv", dtype=str)

    @patch('src.utils.csv_helper.pd.read_csv')
    def test_load_csv_success(self, mock_read_csv):
        mock_df = MagicMock()
        mock_df.columns = []
        mock_read_csv.return_value = mock_df
        df = load_csv_with_unique_key("valid.csv", "TestDB")
        self.assertEqual(df, mock_df)
        mock_read_csv.assert_called_with("valid.csv", dtype=str)

if __name__ == '__main__':
    unittest.main()
