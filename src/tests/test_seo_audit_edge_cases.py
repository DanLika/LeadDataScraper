import pytest
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.scrapers.seo_audit import perform_seo_audit_async

@pytest.mark.asyncio
async def test_perform_seo_audit_async_invalid_url_none():
    result = await perform_seo_audit_async(None)
    assert result == {
        "url": None,
        "is_up": False,
        "score": 0,
        "tech_flags": {},
        "red_flags": ["Invalid URL"]
    }

@pytest.mark.asyncio
async def test_perform_seo_audit_async_invalid_url_empty():
    result = await perform_seo_audit_async("")
    assert result == {
        "url": "",
        "is_up": False,
        "score": 0,
        "tech_flags": {},
        "red_flags": ["Invalid URL"]
    }

@pytest.mark.asyncio
async def test_perform_seo_audit_async_invalid_url_integer():
    result = await perform_seo_audit_async(123)
    assert result == {
        "url": 123,
        "is_up": False,
        "score": 0,
        "tech_flags": {},
        "red_flags": ["Invalid URL"]
    }

@pytest.mark.asyncio
async def test_perform_seo_audit_async_invalid_url_list():
    test_list = ["http://example.com"]
    result = await perform_seo_audit_async(test_list)
    assert result == {
        "url": test_list,
        "is_up": False,
        "score": 0,
        "tech_flags": {},
        "red_flags": ["Invalid URL"]
    }
