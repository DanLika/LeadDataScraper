"""Regression test for the 422→403 schema-leak gate.

FastAPI's default `RequestValidationError` handler embeds the full
Pydantic `detail[]` array (`type`, `loc`, `msg`, `input`, `ctx`) which
leaks the endpoint's expected body shape to anyone who can hit the route
— even without a valid `X-API-Key`. The custom
`_validation_with_authz_check` exception handler in `backend/main.py`
should return a generic 403 to unauthenticated callers, and preserve the
full Pydantic detail array only for authenticated ones (so the frontend's
`detail[].msg` join keeps working for legitimate errors).
"""
import os
import sys
import pytest
from fastapi.testclient import TestClient

backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", "test-key-fixed-for-this-suite")


def test_validation_error_no_api_key_returns_403_not_422_with_schema():
    """Anonymous caller POSTs malformed JSON. Expect 403 + generic
    message — NOT 422 with Pydantic `detail[]` leaking field names."""
    client = TestClient(app)
    res = client.post(
        "/process-lead",
        json={"this-field-does-not-exist": True},
    )
    assert res.status_code == 403
    body = res.json()
    assert body == {"detail": "Invalid or missing API key"}
    # Sanity: the schema-leaking Pydantic structure must NOT appear.
    assert "loc" not in str(body)
    assert "field required" not in str(body).lower()


def test_validation_error_bad_api_key_returns_403_too():
    client = TestClient(app)
    res = client.post(
        "/process-lead",
        json={"this-field-does-not-exist": True},
        headers={"X-API-Key": "wrong-key"},
    )
    assert res.status_code == 403
    assert res.json() == {"detail": "Invalid or missing API key"}


def test_validation_error_authed_still_gets_pydantic_detail():
    """Authenticated caller hitting the same malformed body must still
    receive the structured `detail[]` array so the frontend's
    `AIChat.handleSubmit` can join `detail[].msg` for the user. The
    422-after-auth contract is locked in by tests/test_execute_plan_model.py."""
    client = TestClient(app)
    res = client.post(
        "/process-lead",
        json={"this-field-does-not-exist": True},
        headers={"X-API-Key": "test-key-fixed-for-this-suite"},
    )
    assert res.status_code == 422
    body = res.json()
    assert isinstance(body, dict)
    assert "detail" in body
    assert isinstance(body["detail"], list)
    assert len(body["detail"]) >= 1
    # Pydantic structure preserved
    first = body["detail"][0]
    assert "loc" in first
    assert "msg" in first


def test_validation_error_no_api_key_secret_env_returns_403():
    """Even if the backend was started without `API_SECRET_KEY` set
    (mis-deployment), the gate must still fail closed — return 403,
    not leak the schema via 422. Exact message can come from either
    `verify_api_key` (if FastAPI resolves the route dependency before
    body parsing) or `_validation_with_authz_check` — what matters is
    that the status is 403, the body is JSON, and no Pydantic `loc`
    field-name leak is present."""
    client = TestClient(app)
    saved = os.environ.pop("API_SECRET_KEY", None)
    try:
        res = client.post(
            "/process-lead",
            json={"this-field-does-not-exist": True},
            headers={"X-API-Key": "anything"},
        )
        assert res.status_code == 403
        body = res.json()
        assert "detail" in body
        # Schema leak invariant: no Pydantic structure in the response
        assert "loc" not in str(body)
        assert "field required" not in str(body).lower()
    finally:
        if saved is not None:
            os.environ["API_SECRET_KEY"] = saved
