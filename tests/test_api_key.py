import pytest
import os
import sys
from fastapi import HTTPException
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.main import verify_api_key

@pytest.mark.asyncio
async def test_verify_api_key_valid():
    with patch.dict(os.environ, {"API_SECRET_KEY": "valid_key"}):
        res = await verify_api_key("valid_key")
        assert res == "valid_key"

@pytest.mark.asyncio
async def test_verify_api_key_invalid():
    with patch.dict(os.environ, {"API_SECRET_KEY": "valid_key"}):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key("invalid_key")
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid or missing API key"

@pytest.mark.asyncio
async def test_verify_api_key_missing():
    with patch.dict(os.environ, {"API_SECRET_KEY": "valid_key"}):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(None)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid or missing API key"

@pytest.mark.asyncio
async def test_verify_api_key_not_configured():
    with patch.dict(os.environ, {}, clear=True):
        try:
            res = await verify_api_key("some_key")
            assert res == "no-auth"
        except HTTPException as exc_info:
            assert exc_info.status_code == 403
            assert exc_info.detail == "API Key Verification is not configured"
