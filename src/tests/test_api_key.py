import os
import pytest
from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from unittest.mock import patch
import sys

# Ensure backend can be imported
backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from backend.main import verify_api_key

# We'll create a minimal FastAPI app with the verify_api_key dependency
# to isolate the test from the main app's dependencies like Supabase.
app = FastAPI()

@app.get("/test-endpoint", dependencies=[Depends(verify_api_key)])
async def test_endpoint():
    return {"message": "Success"}

client = TestClient(app)

def test_api_key_not_set_allows_all():
    """Test that when API_SECRET_KEY is not set, the request is allowed."""
    with patch.dict(os.environ, clear=True):
        if "API_SECRET_KEY" in os.environ:
            del os.environ["API_SECRET_KEY"]

        response = client.get("/test-endpoint")
        assert response.status_code == 200
        assert response.json() == {"message": "Success"}

def test_api_key_set_valid_header():
    """Test that when API_SECRET_KEY is set and valid header is provided, the request is allowed."""
    with patch.dict(os.environ, {"API_SECRET_KEY": "my-secret-key"}):
        response = client.get("/test-endpoint", headers={"X-API-Key": "my-secret-key"})
        assert response.status_code == 200
        assert response.json() == {"message": "Success"}

def test_api_key_set_missing_header():
    """Test that when API_SECRET_KEY is set and header is missing, a 403 error is raised."""
    with patch.dict(os.environ, {"API_SECRET_KEY": "my-secret-key"}):
        response = client.get("/test-endpoint")
        assert response.status_code == 403
        assert response.json() == {"detail": "Invalid or missing API key"}

def test_api_key_set_invalid_header():
    """Test that when API_SECRET_KEY is set and invalid header is provided, a 403 error is raised."""
    with patch.dict(os.environ, {"API_SECRET_KEY": "my-secret-key"}):
        response = client.get("/test-endpoint", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 403
        assert response.json() == {"detail": "Invalid or missing API key"}
