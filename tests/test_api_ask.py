import os
import sys
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from backend.main import app

def test_ask_ai_missing_text():
    with patch.dict(os.environ, clear=True):
        client = TestClient(app)

        # Payload with 'instruction' object but missing 'text'
        payload = {
            "instruction": {}
        }

        response = client.post("/ask", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert data["error"] == "Missing 'text' in 'instruction'"

def test_ask_ai_valid_instruction():
    with patch.dict(os.environ, clear=True):
        client = TestClient(app)

        # We need to patch the router call to avoid real AI inference
        with patch("backend.main.router.route_instruction", new_callable=AsyncMock) as mock_route:
            mock_route.return_value = {"task": "UNKNOWN", "response": "mocked plan"}

            payload = {
                "instruction": {
                    "text": "test prompt"
                }
            }

            response = client.post("/ask", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert "plan" in data
            assert data["plan"]["task"] == "UNKNOWN"

def test_ask_ai_missing_instruction_object():
    with patch.dict(os.environ, clear=True):
        client = TestClient(app)

        # Missing 'instruction' altogether
        payload = {}

        response = client.post("/ask", json=payload)

        # Pydantic validation handles this
        assert response.status_code == 422
