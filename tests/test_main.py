import pytest
from backend.main import _is_table_missing_error

def test_is_table_missing_error():
    assert _is_table_missing_error(Exception("PGRST205: relation does not exist")) is True
    assert _is_table_missing_error(Exception("PGRST100: parsing error")) is False
    assert _is_table_missing_error(ValueError("Some other error")) is False
    assert _is_table_missing_error(Exception("")) is False
    assert _is_table_missing_error(ValueError("PGRST205")) is True
