import sys
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.append(os.path.abspath(os.curdir))

def test_cors_middleware_strict_wildcard():
    # Patch os.environ to set ALLOWED_ORIGINS to '*'
    with patch.dict(os.environ, {"ALLOWED_ORIGINS": "*"}):
        # We need to reload or re-import the app to pick up the new env var
        # However, to avoid global state issues and since main.py evaluates env vars at import time,
        # it's best to temporarily remove it from sys.modules
        if 'backend.main' in sys.modules:
            del sys.modules['backend.main']

        from backend.main import app

        client = TestClient(app)
        response = client.options("/", headers={"Origin": "http://evil.com", "Access-Control-Request-Method": "GET"})

        assert response.status_code == 200
        # When allow_credentials is False, access-control-allow-credentials header should be omitted
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers.get("access-control-allow-origin") == "*"

def test_cors_middleware_specific_origin():
    # Patch os.environ to set ALLOWED_ORIGINS to specific URL
    with patch.dict(os.environ, {"ALLOWED_ORIGINS": "http://example.com"}):
        if 'backend.main' in sys.modules:
            del sys.modules['backend.main']

        from backend.main import app

        client = TestClient(app)
        response = client.options("/", headers={"Origin": "http://example.com", "Access-Control-Request-Method": "GET"})

        assert response.status_code == 200
        # When allow_credentials is True, access-control-allow-credentials should be present and 'true'
        assert response.headers.get("access-control-allow-credentials") == "true"
        assert response.headers.get("access-control-allow-origin") == "http://example.com"
