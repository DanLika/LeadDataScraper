import unittest
import pandas as pd
import numpy as np
import os
import tempfile
import sys

# Ensure project root is in the python path
sys.path.append(os.path.abspath(os.curdir))

from src.utils.csv_helper import export_facebook_links

class TestExportFacebookLinks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_path = os.path.join(self.temp_dir.name, "fb_export.csv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_no_facebook_column(self):
        df = pd.DataFrame({'Name': ['A', 'B']})
        result = export_facebook_links(df, self.output_path)
        self.assertEqual(list(result.columns), ['Facebook Link'])
        self.assertEqual(len(result), 0)

        # Verify saved file
        saved_df = pd.read_csv(self.output_path)
        self.assertEqual(list(saved_df.columns), ['Facebook Link'])
        self.assertEqual(len(saved_df), 0)

    def test_with_facebook_column_valid_links(self):
        df = pd.DataFrame({
            'Name': ['A', 'B', 'C', 'D'],
            'facebook': ['https://fb.com/a', 'https://fb.com/b', 'https://fb.com/a', '']
        })
        result = export_facebook_links(df, self.output_path)
        self.assertEqual(list(result.columns), ['Facebook Link'])
        # Should be unique and skip empty
        self.assertEqual(len(result), 2)
        self.assertEqual(list(result['Facebook Link']), ['https://fb.com/a', 'https://fb.com/b'])

        # Verify saved file
        saved_df = pd.read_csv(self.output_path)
        self.assertEqual(list(saved_df.columns), ['Facebook Link'])
        self.assertEqual(len(saved_df), 2)

    def test_with_invalid_facebook_entries(self):
        df = pd.DataFrame({
            'facebook': ['nan', 'None', 'no social found', '', np.nan, 'https://fb.com/valid']
        })
        result = export_facebook_links(df, self.output_path)
        self.assertEqual(list(result.columns), ['Facebook Link'])
        # Should filter out all the invalid ones
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result['Facebook Link']), ['https://fb.com/valid'])

if __name__ == '__main__':
    unittest.main()
