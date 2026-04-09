import unittest
import pandas as pd
import os
from unittest.mock import patch
import sys
sys.path.append(os.path.abspath(os.curdir))

from src.utils.csv_helper import export_outreach_ready_csv

class TestExportOutreachReadyCSV(unittest.TestCase):

    @patch('src.utils.csv_helper.save_csv')
    def test_basic_mapping_and_dropping_missing_emails(self, mock_save):
        data = {
            'email': ['test1@example.com', '', None, 'test4@example.com'],
            'Website': ['site1.com', 'site2.com', 'site3.com', 'site4.com'],
            'segment': ['cat1', 'cat2', 'cat3', 'cat4'],
            'first_name': ['John', 'Jane', 'Doe', 'Smith'],
            'Address': ['Loc 1', 'Loc 2', 'Loc 3', 'Loc 4'],
            'pain_points': [['pain1', 'pain2'], 'pain3', None, ['pain4']]
        }
        df = pd.DataFrame(data)

        output_path = 'dummy_path.csv'
        result_df = export_outreach_ready_csv(df, output_path)

        # Check mock call
        mock_save.assert_called_once_with(result_df, output_path)

        # Check length (only 2 valid emails)
        self.assertEqual(len(result_df), 2)

        # Check columns order and existence
        expected_cols = ['email', 'website', 'category', 'first_name', 'location', 'pain_point']
        self.assertEqual(list(result_df.columns), expected_cols)

        # Check mapped values
        self.assertEqual(result_df.iloc[0]['email'], 'test1@example.com')
        self.assertEqual(result_df.iloc[0]['website'], 'site1.com')
        self.assertEqual(result_df.iloc[0]['category'], 'cat1')
        self.assertEqual(result_df.iloc[0]['first_name'], 'John')
        self.assertEqual(result_df.iloc[0]['location'], 'Loc 1')
        self.assertEqual(result_df.iloc[0]['pain_point'], 'pain1, pain2')

        self.assertEqual(result_df.iloc[1]['email'], 'test4@example.com')
        self.assertEqual(result_df.iloc[1]['pain_point'], 'pain4')

    @patch('src.utils.csv_helper.save_csv')
    def test_missing_optional_columns(self, mock_save):
        data = {
            'email': ['test@example.com'],
        }
        df = pd.DataFrame(data)

        result_df = export_outreach_ready_csv(df, 'dummy.csv')

        # Missing columns should be added as empty strings
        for col in ['website', 'category', 'first_name', 'location', 'pain_point']:
            self.assertEqual(result_df.iloc[0][col], "")

    @patch('src.utils.csv_helper.save_csv')
    def test_uppercase_pain_points_and_alternate_mappings(self, mock_save):
        data = {
            'email': ['test@example.com'],
            'website': ['lower.com'], # uses lowercase website
            'address': ['lower loc'], # uses lowercase address
            'PAIN_POINTS': ['BIG PAIN']
        }
        df = pd.DataFrame(data)

        result_df = export_outreach_ready_csv(df, 'dummy.csv')

        self.assertEqual(result_df.iloc[0]['website'], 'lower.com')
        self.assertEqual(result_df.iloc[0]['location'], 'lower loc')
        self.assertEqual(result_df.iloc[0]['pain_point'], 'BIG PAIN')

if __name__ == '__main__':
    unittest.main()
