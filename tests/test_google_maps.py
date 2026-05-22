import pytest
import numpy as np
from src.processors.google_maps import clean_website

def test_clean_website_none():
    assert np.isnan(clean_website(None))

def test_clean_website_non_string():
    assert np.isnan(clean_website(123))
    assert np.isnan(clean_website(3.14))

def test_clean_website_empty_or_whitespace():
    assert np.isnan(clean_website(""))
    assert np.isnan(clean_website("   "))

def test_clean_website_www_prefix():
    assert clean_website("www.example.com") == "http://www.example.com"
    assert clean_website("www.example.com/path") == "http://www.example.com/path"

def test_clean_website_http_prefix():
    assert clean_website("http://www.example.com") == "http://www.example.com"
    assert clean_website("http://example.com") == "http://example.com"

def test_clean_website_https_prefix():
    assert clean_website("https://www.example.com") == "https://www.example.com"
    assert clean_website("https://example.com") == "https://example.com"

def test_clean_website_no_prefix():
    assert clean_website("example.com") == "example.com"

def test_clean_website_with_whitespace():
    assert clean_website("  www.example.com  ") == "http://www.example.com"
    assert clean_website("  https://example.com  ") == "https://example.com"
