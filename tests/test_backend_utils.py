import pandas as pd
import os
import sys
import pytest

# Add current dir to path to import backend
sys.path.append(os.path.abspath(os.curdir))

from backend.main import _filter_valid_columns

def test_filter_valid_columns_mixed():
    """Test with a mix of valid and invalid columns."""
    data = {
        "unique_key": ["lead_1"],
        "name": ["John Doe"],
        "company_name": ["Acme Corp"],
        "invalid_col_1": ["ignore this"],
        "website": ["acme.com"],
        "another_invalid_col": [123],
    }
    df_input = pd.DataFrame(data)

    df_output = _filter_valid_columns(df_input)

    expected_cols = ["unique_key", "name", "company_name", "website"]

    # Assert output has exactly the expected columns
    assert set(df_output.columns) == set(expected_cols)
    # Ensure data hasn't been lost
    assert len(df_output) == 1
    assert df_output["unique_key"].iloc[0] == "lead_1"

def test_filter_valid_columns_all_valid():
    """Test when all columns are valid."""
    data = {
        "unique_key": ["lead_1"],
        "name": ["John Doe"],
        "email": ["test@test.com"]
    }
    df_input = pd.DataFrame(data)
    df_output = _filter_valid_columns(df_input)

    # Output should exactly match input
    assert set(df_output.columns) == set(df_input.columns)
    assert len(df_output) == 1

def test_filter_valid_columns_all_invalid():
    """Test when all columns are invalid."""
    data = {
        "random_col_1": ["data"],
        "random_col_2": ["more_data"]
    }
    df_input = pd.DataFrame(data)
    df_output = _filter_valid_columns(df_input)

    # Output should have no columns
    assert len(df_output.columns) == 0
    assert len(df_output) == 1 # still has 1 row but no columns

def test_filter_valid_columns_empty():
    """Test with an empty dataframe."""
    df_input = pd.DataFrame(columns=["unique_key", "invalid_col"])
    df_output = _filter_valid_columns(df_input)

    assert set(df_output.columns) == {"unique_key"}
    assert len(df_output) == 0
