import json
from fastapi.responses import JSONResponse

from backend.main import error_response

def test_error_response_default_status():
    """Test error_response helper with the default status code (500)."""
    message = "Internal Server Error"
    response = error_response(message)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 500

    # JSONResponse.body is bytes, so we need to decode and parse it
    body_data = json.loads(response.body.decode("utf-8"))
    assert body_data == {"error": message}

def test_error_response_custom_status():
    """Test error_response helper with a custom status code (e.g., 404)."""
    message = "Not Found"
    status_code = 404
    response = error_response(message, status_code=status_code)

    assert isinstance(response, JSONResponse)
    assert response.status_code == status_code

    body_data = json.loads(response.body.decode("utf-8"))
    assert body_data == {"error": message}

def test_error_response_empty_message():
    """Test error_response helper with an empty message."""
    message = ""
    status_code = 400
    response = error_response(message, status_code=status_code)

    assert isinstance(response, JSONResponse)
    assert response.status_code == status_code

    body_data = json.loads(response.body.decode("utf-8"))
    assert body_data == {"error": message}
