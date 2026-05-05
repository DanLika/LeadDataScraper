import sys
import os
import json

sys.path.append(os.path.abspath(os.curdir))

from backend.main import error_response

def test_error_response_default_status():
    response = error_response("An error occurred")
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body == {"error": "An error occurred"}

def test_error_response_custom_status():
    response = error_response("Not found", status_code=404)
    assert response.status_code == 404
    body = json.loads(response.body)
    assert body == {"error": "Not found"}

def test_error_response_dict_message():
    response = error_response({"details": "complex error"})
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body == {"error": {"details": "complex error"}}
