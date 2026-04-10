import unittest
from unittest.mock import patch
import os
import tempfile
import pandas as pd

from src.utils.csv_helper import save_csv

class TestSaveCSV(unittest.TestCase):

    def test_save_csv_success(self):
        df = pd.DataFrame({'col1': [1, 2], 'col2': ['a', 'b']})
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.csv")
            save_csv(df, filepath)

            self.assertTrue(os.path.exists(filepath))
            loaded_df = pd.read_csv(filepath)
            pd.testing.assert_frame_equal(df, loaded_df)

    def test_save_csv_creates_directory(self):
        df = pd.DataFrame({'col1': [1]})
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "newdir", "test.csv")

            self.assertFalse(os.path.exists(os.path.dirname(filepath)))
            save_csv(df, filepath)

            self.assertTrue(os.path.exists(os.path.dirname(filepath)))
            self.assertTrue(os.path.exists(filepath))

    @patch('src.utils.csv_helper.logger')
    def test_save_csv_logging(self, mock_logger):
        df = pd.DataFrame({'col1': [1]})
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.csv")
            save_csv(df, filepath)
            mock_logger.info.assert_called_once_with("Data saved to '%s'.", filepath)

    @patch('src.utils.csv_helper.pd.DataFrame.to_csv')
    def test_save_csv_error_propagation(self, mock_to_csv):
        mock_to_csv.side_effect = PermissionError("Permission denied")
        df = pd.DataFrame({'col1': [1]})

        with self.assertRaises(PermissionError):
            save_csv(df, "dummy.csv")

if __name__ == '__main__':
    unittest.main()
