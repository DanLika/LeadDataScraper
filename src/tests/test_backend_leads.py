import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.curdir))

# Mock environment variables to allow backend to load without actual API keys
os.environ["API_SECRET_KEY"] = "test_key"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"

from fastapi.testclient import TestClient
import backend.main

@pytest.fixture
def mock_db():
    with patch('backend.main.db') as mock_db:
        yield mock_db

def test_list_leads_db_not_connected(mock_db):
    from backend.main import app
    client = TestClient(app)

    mock_db.client = None

    response = client.get("/leads", headers={"X-API-Key": "test_key"})

    assert response.status_code == 503
    assert response.json() == {"error": "Database not connected"}
