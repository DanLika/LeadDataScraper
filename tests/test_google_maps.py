import pytest
import numpy as np
import pandas as pd
from src.processors.google_maps import clean_phone

@pytest.mark.parametrize("input_phone, expected", [
    # Valid USA/Canadian formats
    ('(555) 123-4567', '5551234567'),
    ('555-123-4567', '5551234567'),
    ('555.123.4567', '5551234567'),
    ('555 123 4567', '5551234567'),
    ('5551234567', '5551234567'),

    # Valid International formats
    ('+1 555-123-4567', '+15551234567'),
    ('+44 20 7123 4567', '+442071234567'),
    ('+1 (555) 123-4567', '+15551234567'),
    ('+91 98765 43210', '+919876543210'),

    # Letters mixed with numbers
    ('1-800-CALL-NOW', np.nan), # Only keeps digits, >7 length check will fail
    ('1-800-2255-669', '18002255669'),

    # Too short numbers
    ('123456', np.nan),
    ('+123', np.nan),
    ('555', np.nan),
    ('', np.nan),
    ('   ', np.nan),

    # Invalid types
    (None, np.nan),
    (np.nan, np.nan),
    (1234567890, np.nan),
])
def test_clean_phone(input_phone, expected):
    """
    Test extraction and cleaning of phone numbers with various edge cases.
    """
    result = clean_phone(input_phone)
    if pd.isna(expected):
        assert pd.isna(result)
    else:
        assert result == expected
