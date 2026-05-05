import pandas as pd
import os
import sys

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

from src.processors.google_maps import process_gmaps_df
from src.utils.csv_helper import merge_and_deduplicate

def test_gmaps_processing():
    print("Testing Google Maps Processing...")
    data = {
        'hfpxzc href': ['https://maps.google.com/1', 'https://maps.google.com/2'],
        'qBF1Pd': ['Company A', 'Company B'],
        'MW4etd': ['4,5', '3.8'],
        'UY7F9': ['(120)', '(50)'],
        'lcr4fd href': ['www.comp-a.com', 'https://comp-b.com'],
        'UsdlK': ['+381 11 222333', '064/123-456']
    }
    df_raw = pd.DataFrame(data)
    df_processed = process_gmaps_df(df_raw, "Test Source")
    
    print(f"Processed Columns: {df_processed.columns.tolist()}")
    print(f"Sample unique_key: {df_processed['unique_key'].iloc[0]}")
    assert 'Name' in df_processed.columns
    assert 'Website' in df_processed.columns
    assert df_processed['Website'].iloc[0] == 'http://www.comp-a.com'
    print("✅ Google Maps processing test passed!")

def test_deduplication():
    print("\nTesting Deduplication...")
    df1 = pd.DataFrame({'Name': ['A', 'B'], 'unique_key': ['key1', 'key2']})
    df2 = pd.DataFrame({'Name': ['A', 'C'], 'unique_key': ['key1', 'key3']})
    
    final = merge_and_deduplicate([df1, df2])
    print(f"Final Count: {len(final)}")
    assert len(final) == 3

    # Test empty list edge case
    final_empty = merge_and_deduplicate([])
    assert final_empty.empty, "merge_and_deduplicate should return an empty DataFrame for empty input"
    print("✅ Deduplication test passed!")

if __name__ == "__main__":
    test_gmaps_processing()
    test_deduplication()
