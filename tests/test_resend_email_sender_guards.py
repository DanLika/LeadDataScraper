"""Regression tests for the ResendEmailSender HTTP API client.

ResendEmailSender is the planned replacement for SMTPEmailSender once
DNS + Resend account are live (see ``docs/email-deliverability.md`` +
``docs/email-dispatch-architecture.md``). Until then it's dark-launched
behind ``EMAIL_PROVIDER=resend_api`` — SMTP remains the default.

These tests mirror ``test_email_sender_guards.py`` (SMTP path) plus:
  - CRLF reject on ``reply_to`` (new field, not on the SMTP class)
  - ``Idempotency-Key`` header propagation
  - Resend response-code → status-dict mapping (200 / 401 / 422 / 429 / 5xx)
  - Network timeout + connection error handling
  - Factory dispatch via ``EMAIL_PROVIDER`` env

A future wiring change MUST not loosen any of these guards.
"""

import asyncio
from typing import Any, Optional
from unittest.mock import patch

import aiohttp
import pytest

from src.integrations.email_sender import (
    ResendEmailSender,
    SMTPEmailSender,
    get_email_sender,
)


# ────────────────────── fake aiohttp transport ──────────────────────


class _FakeResponse:
    def __init__(self, status: int, body: Optional[dict] = None):
        self.status = status
        self._body = body or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self._body


class _FakeSession:
    def __init__(
        self,
        response: Optional[_FakeResponse] = None,
        post_exception: Optional[Exception] = None,
    ):
        self._response = response
        self._post_exception = post_exception
        self.captured: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def post(self, url: str, *, headers=None, json=None):
        self.captured.append(
            {"url": url, "headers": dict(headers or {}), "json": dict(json or {})}
        )
        if self._post_exception:
            raise self._post_exception
        return self._response


def _configured_sender() -> ResendEmailSender:
    """A ResendEmailSender past the credentials gate, so downstream
    validation is what the test actually exercises."""
    s = ResendEmailSender()
    s.api_key = "re_fake_key_for_test"
    s.from_email = "outreach@mail.leaddatascraper.com"
    s.from_name = "Operator"
    s.reply_to = ""
    s.list_unsubscribe = ""
    return s


def _run(coro):
    return asyncio.run(coro)


async def _noop_rate_limit():
    return None


def _patch_session(fake: _FakeSession):
    return patch(
        "src.integrations.email_sender.aiohttp.ClientSession",
        return_value=fake,
    )


# ────────────────────── credentials gate ──────────────────────


def test_missing_api_key_short_circuits():
    s = ResendEmailSender()
    s.api_key = ""
    s.from_email = "ok@example.com"
    r = _run(s.send("a@b.com", "S", "B"))
    assert r["status"] == "error"
    assert "credentials" in r["error"].lower()


def test_missing_from_email_short_circuits():
    s = ResendEmailSender()
    s.api_key = "re_key"
    s.from_email = ""
    r = _run(s.send("a@b.com", "S", "B"))
    assert r["status"] == "error"
    assert "credentials" in r["error"].lower()


# ────────────── recipient regex — header-injection guard ──────────────


def test_recipient_crlf_bcc_injection_rejected():
    s = _configured_sender()
    r = _run(s.send("victim@x.com\r\nBcc: attacker@evil.com", "S", "B"))
    assert r["status"] == "error"
    assert r["error"] == "Invalid email format."


def test_recipient_newline_only_injection_rejected():
    s = _configured_sender()
    r = _run(s.send("victim@x.com\nCc: attacker@evil.com", "S", "B"))
    assert r["status"] == "error"
    assert r["error"] == "Invalid email format."


def test_recipient_malformed_addresses_rejected():
    s = _configured_sender()
    for bad in [
        "no-at-sign",
        "@no-local.com",
        "no-tld@example",
        "two@@at.com",
        "trailing space @x.com",
        "tab\tinside@x.com",
        "",
    ]:
        r = _run(s.send(bad, "S", "B"))
        assert r["status"] == "error", f"{bad!r} should be rejected"
        assert r["error"] == "Invalid email format."


def test_bounced_recipient_short_circuits():
    s = _configured_sender()
    s.bounced_emails.add("dead@example.com")
    r = _run(s.send("dead@example.com", "S", "B"))
    assert r["status"] == "bounced"


# ──────────── CRLF guard on subject + from_name + reply_to ────────────


def test_subject_crlf_rejected():
    s = _configured_sender()
    for evil in [
        "Subject\r\nBcc: attacker@evil.com",
        "Subject\nX-Injected: 1",
        "Subject\rmore",
    ]:
        r = _run(s.send("ok@example.com", evil, "Body"))
        assert r["status"] == "error"
        assert "CRLF" in r["error"]


def test_from_name_crlf_rejected():
    s = _configured_sender()
    r = _run(
        s.send("ok@example.com", "Clean", "Body", from_name="Evil\r\nBcc: x")
    )
    assert r["status"] == "error"
    assert "CRLF" in r["error"]


def test_reply_to_crlf_rejected():
    """SMTP path has no reply_to. Resend does — CRLF reject on this
    new field is its own regression guard."""
    s = _configured_sender()
    s.reply_to = "Reply\r\nBcc: attacker@evil.com"
    r = _run(s.send("ok@example.com", "S", "B"))
    assert r["status"] == "error"
    assert "CRLF" in r["error"]


# ───────────────── positive path — 200 success ─────────────────


def test_success_200_returns_provider_message_id():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_abc123"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("lead@example.com", "Quick question", "Hello"))

    assert r["status"] == "sent"
    assert r["to"] == "lead@example.com"
    assert r["provider_message_id"] == "msg_abc123"
    assert "sent_at" in r

    assert len(fake.captured) == 1
    req = fake.captured[0]
    assert req["url"] == "https://api.resend.com/emails"
    assert req["headers"]["Authorization"] == "Bearer re_fake_key_for_test"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["json"]["to"] == ["lead@example.com"]
    assert req["json"]["subject"] == "Quick question"
    assert req["json"]["text"] == "Hello"
    assert req["json"]["from"] == "Operator <outreach@mail.leaddatascraper.com>"


def test_idempotency_key_passed_to_provider():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B", idempotency_key="campaign-msg-42"))

    assert fake.captured[0]["headers"]["Idempotency-Key"] == "campaign-msg-42"


def test_idempotency_key_absent_when_not_provided():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B"))

    assert "Idempotency-Key" not in fake.captured[0]["headers"]


def test_reply_to_added_to_payload_when_set():
    s = _configured_sender()
    s.reply_to = "ops@bookbed.io"
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B"))

    assert fake.captured[0]["json"]["reply_to"] == "ops@bookbed.io"


def test_reply_to_absent_when_unset():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B"))

    assert "reply_to" not in fake.captured[0]["json"]


def test_list_unsubscribe_headers_added_when_set():
    s = _configured_sender()
    s.list_unsubscribe = "<mailto:unsub@bookbed.io>"
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B"))

    payload_headers = fake.captured[0]["json"].get("headers", {})
    assert payload_headers.get("List-Unsubscribe") == "<mailto:unsub@bookbed.io>"
    assert payload_headers.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"


def test_list_unsubscribe_absent_when_unset():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(200, {"id": "msg_x"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        _run(s.send("ok@example.com", "S", "B"))

    assert "headers" not in fake.captured[0]["json"]


# ───────────────── error response mapping ─────────────────


def test_401_maps_to_auth_failed():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(401, {"name": "invalid_api_key", "message": "Invalid"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "authentication" in r["error"].lower()


def test_422_maps_to_validation_with_message():
    s = _configured_sender()
    fake = _FakeSession(
        _FakeResponse(422, {"name": "validation_error", "message": "domain not verified"})
    )

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "Validation" in r["error"]
    assert "domain not verified" in r["error"]


def test_422_with_no_message_falls_back_to_unknown():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(422, {}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "unknown" in r["error"].lower()


def test_429_maps_to_rate_limited():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(429, {"name": "rate_limit_exceeded"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "rate_limited"


def test_500_maps_to_provider_error():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(500))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "500" in r["error"]


def test_503_maps_to_provider_error():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(503))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "503" in r["error"]


def test_unexpected_3xx_maps_to_unexpected():
    s = _configured_sender()
    fake = _FakeSession(_FakeResponse(302))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "Unexpected" in r["error"] or "302" in r["error"]


def test_timeout_maps_to_timeout_error():
    s = _configured_sender()
    fake = _FakeSession(post_exception=asyncio.TimeoutError())

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "timeout" in r["error"].lower()


def test_network_error_maps_to_provider_unreachable():
    s = _configured_sender()
    fake = _FakeSession(post_exception=aiohttp.ClientConnectionError("conn refused"))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert r["status"] == "error"
    assert "network" in r["error"].lower() or "provider" in r["error"].lower()


# ───────────────────── error string sanitization ─────────────────────


def test_error_strings_never_echo_api_key():
    """Defense in depth — even though we control every error path, lock
    in that the API key never appears in any returned error message."""
    s = _configured_sender()
    s.api_key = "re_super_secret_key_xyz"
    fake = _FakeSession(_FakeResponse(500, {"message": "internal"}))

    with _patch_session(fake), patch.object(s, "_wait_for_rate_limit", _noop_rate_limit):
        r = _run(s.send("ok@example.com", "S", "B"))

    assert "re_super_secret_key_xyz" not in str(r)


# ───────────────────────── factory ─────────────────────────


def test_factory_defaults_to_smtp(monkeypatch):
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    sender = get_email_sender()
    assert isinstance(sender, SMTPEmailSender)


def test_factory_returns_resend_when_requested(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "resend_api")
    sender = get_email_sender()
    assert isinstance(sender, ResendEmailSender)


def test_factory_empty_string_treated_as_smtp(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "")
    sender = get_email_sender()
    assert isinstance(sender, SMTPEmailSender)


def test_factory_raises_on_unknown_provider(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    with pytest.raises(ValueError, match="Unknown EMAIL_PROVIDER"):
        get_email_sender()


def test_factory_lowercase_normalizes(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "RESEND_API")
    sender = get_email_sender()
    assert isinstance(sender, ResendEmailSender)
