"""Real-behavior tests for the `clean_website` / `clean_phone` cell
cleaners in `src/processors/google_maps.py` — the non-string and
short-number branches were uncovered.
"""

import numpy as np
import pandas as pd

from src.processors.google_maps import clean_website, clean_phone


# ─────────────────────────── clean_website ───────────────────────────


def test_clean_website_non_string_returns_nan():
    assert clean_website(None) is np.nan or pd.isna(clean_website(None))
    assert pd.isna(clean_website(12345))
    assert pd.isna(clean_website(["x"]))


def test_clean_website_blank_returns_nan():
    assert pd.isna(clean_website(""))
    assert pd.isna(clean_website("   "))


def test_clean_website_www_without_scheme_gets_http_prefix():
    assert clean_website("www.example.com") == "http://www.example.com"


def test_clean_website_already_has_scheme_unchanged():
    assert clean_website("https://example.com") == "https://example.com"
    assert clean_website("http://www.example.com") == "http://www.example.com"


def test_clean_website_trims_whitespace():
    assert clean_website("  https://example.com  ") == "https://example.com"


# ──────────────────────────── clean_phone ────────────────────────────


def test_clean_phone_non_string_returns_nan():
    assert pd.isna(clean_phone(None))
    assert pd.isna(clean_phone(387123456))


def test_clean_phone_blank_returns_nan():
    assert pd.isna(clean_phone(""))
    assert pd.isna(clean_phone("   "))


def test_clean_phone_too_few_digits_returns_nan():
    # fewer than 7 digits → not a real phone number
    assert pd.isna(clean_phone("12345"))
    assert pd.isna(clean_phone("(01) 23"))


def test_clean_phone_valid_local_number_cleaned():
    out = clean_phone("(033) 555-111")
    assert out == "033555111"


def test_clean_phone_preserves_leading_plus_for_international():
    out = clean_phone("+387 33 555 111")
    assert out.startswith("+")
    assert out == "+38733555111"
