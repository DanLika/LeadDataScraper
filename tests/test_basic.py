import pandas as pd
import os
import sys

# Add current dir to path to import src
sys.path.append(os.path.abspath(os.curdir))

from src.utils.csv_helper import merge_and_deduplicate

def test_deduplication():
    print("\nTesting Deduplication...")
    df1 = pd.DataFrame({'Name': ['A', 'B'], 'unique_key': ['key1', 'key2']})
    df2 = pd.DataFrame({'Name': ['A', 'C'], 'unique_key': ['key1', 'key3']})
    
    final = merge_and_deduplicate([df1, df2])
    print(f"Final Count: {len(final)}")
    assert len(final) == 3
    print("✅ Deduplication test passed!")

if __name__ == "__main__":
    test_deduplication()
