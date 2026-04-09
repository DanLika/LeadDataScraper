import unittest
import pandas as pd
from unittest.mock import patch
import os
import sys

sys.path.append(os.path.abspath(os.curdir))
from src.utils.csv_helper import export_facebook_links

class TestCSVHelper(unittest.TestCase):

    @patch('src.utils.csv_helper.save_csv')
    def test_export_facebook_links_missing_facebook_column(self, mock_save_csv):
        # Create a real DataFrame representing one without a 'facebook' column
        real_df = pd.DataFrame({'Name': ['Test Company'], 'Website': ['http://test.com']})

        # Call the function
        result_df = export_facebook_links(real_df, "dummy_path.csv")

        # Verify it returns an empty DataFrame with just the 'Facebook Link' column
        self.assertEqual(list(result_df.columns), ['Facebook Link'])
        self.assertTrue(result_df.empty)

        # Verify save_csv was called with the correct DataFrame
        mock_save_csv.assert_called_once()
        args, kwargs = mock_save_csv.call_args
        saved_df = args[0]
        self.assertEqual(list(saved_df.columns), ['Facebook Link'])
        self.assertTrue(saved_df.empty)
        self.assertEqual(args[1], "dummy_path.csv")

if __name__ == '__main__':
    unittest.main()
