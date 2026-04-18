import unittest
import pandas as pd
import numpy as np
from unittest.mock import patch
from src.utils.csv_helper import export_outreach_ready_csv, export_facebook_links

class TestCSVHelperExport(unittest.TestCase):

    @patch('src.utils.csv_helper.save_csv')
    def test_export_outreach_ready_csv_mapping_and_filtering(self, mock_save_csv):
        # Create a test dataframe with mixed columns and empty/nan emails
        data = {
            'email': ['test1@example.com', '', np.nan, 'test4@example.com'],
            'Website': ['www.test1.com', 'www.test2.com', 'www.test3.com', 'www.test4.com'],
            'segment': ['cat1', 'cat2', 'cat3', 'cat4'],
            'first_name': ['John', 'Jane', 'Doe', 'Smith'],
            'Address': ['Loc 1', 'Loc 2', 'Loc 3', 'Loc 4'],
            'pain_points': [['pain1', 'pain2'], 'pain3', np.nan, ['pain4']]
        }
        df = pd.DataFrame(data)

        # Call the function
        result_df = export_outreach_ready_csv(df, 'dummy_path.csv')

        # Verify that save_csv was called correctly
        mock_save_csv.assert_called_once()
        self.assertEqual(mock_save_csv.call_args[0][1], 'dummy_path.csv')

        # Check filtering: only rows with valid emails should remain (indices 0 and 3)
        self.assertEqual(len(result_df), 2)
        self.assertListEqual(result_df['email'].tolist(), ['test1@example.com', 'test4@example.com'])

        # Check column mapping and required columns presence and order
        expected_cols = ['email', 'website', 'category', 'first_name', 'location', 'pain_point']
        self.assertListEqual(list(result_df.columns), expected_cols)

        # Check mapped values
        self.assertListEqual(result_df['website'].tolist(), ['www.test1.com', 'www.test4.com'])
        self.assertListEqual(result_df['category'].tolist(), ['cat1', 'cat4'])
        self.assertListEqual(result_df['location'].tolist(), ['Loc 1', 'Loc 4'])

        # Check pain_points processing
        self.assertListEqual(result_df['pain_point'].tolist(), ['pain1, pain2', 'pain4'])

    @patch('src.utils.csv_helper.save_csv')
    def test_export_outreach_ready_csv_no_pain_points(self, mock_save_csv):
        data = {
            'email': ['test1@example.com'],
            'Website': ['www.test1.com'],
        }
        df = pd.DataFrame(data)

        result_df = export_outreach_ready_csv(df, 'dummy_path.csv')

        # Check required columns are present even if missing in source
        expected_cols = ['email', 'website', 'category', 'first_name', 'location', 'pain_point']
        self.assertListEqual(list(result_df.columns), expected_cols)

        # Check pain_point is empty string
        self.assertEqual(result_df['pain_point'].iloc[0], '')
        self.assertEqual(result_df['category'].iloc[0], '')

    @patch('src.utils.csv_helper.save_csv')
    def test_export_facebook_links_with_valid_and_invalid_links(self, mock_save_csv):
        data = {
            'facebook': [
                'https://facebook.com/1',
                'https://facebook.com/2',
                'https://facebook.com/1', # Duplicate
                'nan',
                'None',
                'no social found',
                '',
                np.nan
            ]
        }
        df = pd.DataFrame(data)

        result_df = export_facebook_links(df, 'dummy_path.csv')

        mock_save_csv.assert_called_once()
        self.assertEqual(mock_save_csv.call_args[0][1], 'dummy_path.csv')

        # Check that invalid links are filtered and duplicates are removed
        expected_links = ['https://facebook.com/1', 'https://facebook.com/2']
        self.assertListEqual(result_df['Facebook Link'].tolist(), expected_links)

    @patch('src.utils.csv_helper.save_csv')
    def test_export_facebook_links_no_facebook_column(self, mock_save_csv):
        data = {
            'other_column': ['data1', 'data2']
        }
        df = pd.DataFrame(data)

        result_df = export_facebook_links(df, 'dummy_path.csv')

        # Should return an empty dataframe with 'Facebook Link' column
        self.assertTrue(result_df.empty)
        self.assertListEqual(list(result_df.columns), ['Facebook Link'])


if __name__ == '__main__':
    unittest.main()
