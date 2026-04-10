import pytest
import numpy as np
import sys
import os

# Add the project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.processors.google_maps import clean_website

@pytest.mark.parametrize("input_url, expected", [
    ("www.example.com", "http://www.example.com"),
    ("  www.example.com  ", "http://www.example.com"),
    ("http://example.com", "http://example.com"),
    ("https://example.com", "https://example.com"),
    ("example.com", "example.com"),
    ("  ", np.nan),
    (None, np.nan),
    (123, np.nan),
    (np.nan, np.nan)
])
def test_clean_website(input_url, expected):
    result = clean_website(input_url)

    # We need to handle np.nan comparison carefully as np.nan == np.nan is False
    if isinstance(expected, float) and np.isnan(expected):
        assert isinstance(result, float) and np.isnan(result)
    else:
        assert result == expected
