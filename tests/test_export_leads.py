import sys
from unittest.mock import MagicMock

# Mock dependencies to avoid ModuleNotFoundError in restricted environments
if 'pandas' not in sys.modules:
    sys.modules['pandas'] = MagicMock()
if 'src.utils.supabase_helper' not in sys.modules:
    sys.modules['src.utils.supabase_helper'] = MagicMock()
if 'src.utils.csv_helper' not in sys.modules:
    sys.modules['src.utils.csv_helper'] = MagicMock()

import pytest
from src.scripts.export_leads import is_outreach_ready

def test_is_outreach_ready_happy_path():
    # Has email and score > 30
    row = {'email': 'test@example.com', 'outreach_score': 31}
    assert is_outreach_ready(row) is True

    # Has phone and score > 30
    row = {'phone': '1234567890', 'outreach_score': 50}
    assert is_outreach_ready(row) is True

    # Has both and score > 30
    row = {'email': 'test@example.com', 'phone': '1234567890', 'outreach_score': 100}
    assert is_outreach_ready(row) is True

def test_is_outreach_ready_missing_contact():
    # Score > 30 but no contact info
    row = {'outreach_score': 50}
    assert is_outreach_ready(row) is False

    # Empty strings for contact info
    row = {'email': '', 'phone': '', 'outreach_score': 50}
    assert is_outreach_ready(row) is False

    # None for contact info
    row = {'email': None, 'phone': None, 'outreach_score': 50}
    assert is_outreach_ready(row) is False

def test_is_outreach_ready_score_threshold():
    # Exact threshold (should be > 30)
    row = {'email': 'test@example.com', 'outreach_score': 30}
    assert is_outreach_ready(row) is False

    # Below threshold
    row = {'email': 'test@example.com', 'outreach_score': 29.9}
    assert is_outreach_ready(row) is False

    # Missing score entirely
    row = {'email': 'test@example.com'}
    assert is_outreach_ready(row) is False

def test_is_outreach_ready_score_parsing():
    # String representation of valid float
    row = {'email': 'test@example.com', 'outreach_score': '40.5'}
    assert is_outreach_ready(row) is True

    # String representation of invalid float
    row = {'email': 'test@example.com', 'outreach_score': 'invalid'}
    assert is_outreach_ready(row) is False

    # None as score
    row = {'email': 'test@example.com', 'outreach_score': None}
    assert is_outreach_ready(row) is False

    # Object as score causing TypeError
    row = {'email': 'test@example.com', 'outreach_score': {}}
    assert is_outreach_ready(row) is False
