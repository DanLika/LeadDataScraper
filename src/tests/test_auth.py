import pytest
import os
import sys
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.append(os.path.abspath(os.curdir))

from backend.main import verify_api_key, app

client = TestClient(app)

@pytest.mark.asyncio
async def test_verify_api_key_missing_env():
    with patch.dict(os.environ, {}, clear=True):
        # Ensure API_SECRET_KEY is not set
        if "API_SECRET_KEY" in os.environ:
            del os.environ["API_SECRET_KEY"]

        result = await verify_api_key(key="any-key")
        assert result == "no-auth"

@pytest.mark.asyncio
async def test_verify_api_key_valid():
    with patch.dict(os.environ, {"API_SECRET_KEY": "my-secret-key"}):
        result = await verify_api_key(key="my-secret-key")
        assert result == "my-secret-key"

@pytest.mark.asyncio
async def test_verify_api_key_invalid():
    with patch.dict(os.environ, {"API_SECRET_KEY": "my-secret-key"}):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(key="wrong-key")
        assert exc_info.value.status_code == 403
