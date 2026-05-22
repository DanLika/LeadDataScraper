"""Regression tests for the SMTP header-injection guards in
`src/integrations/email_sender.py`.

`SMTPEmailSender` is implemented but not yet wired to an endpoint.
When it is, these guards are the boundary that stops a poisoned lead
row from injecting mail headers:

- the recipient regex `^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$` — `[^@\\s]`
  excludes whitespace so `victim@x.com\\r\\nBcc: attacker@evil` cannot
  smuggle Cc/Bcc/Subject through `msg["To"]`;
- the CRLF check on `subject` + `from_name` before they are written
  into MIME headers (subject is Gemini-drafted from attacker-controlled
  lead fields).

Tightened during the 2026-05 audit; this file locks the behaviour so a
future wiring change can't regress it.
"""
import asyncio
from unittest.mock import patch

from src.integrations.email_sender import SMTPEmailSender


def _configured_sender():
    """An SMTPEmailSender past the credentials gate, so the recipient /
    CRLF validation downstream is what the test actually exercises."""
    s = SMTPEmailSender()
    s.smtp_user = "ops@example.com"
    s.smtp_pass = "dummy-pass"
    s.from_email = "ops@example.com"
    return s


def _run(coro):
    return asyncio.run(coro)


# ───────────────────────── credentials gate ──────────────────────────

def test_missing_credentials_short_circuits():
    s = SMTPEmailSender()
    s.smtp_user = ""
    s.smtp_pass = ""
    r = _run(s.send("a@b.com", "Subject", "Body"))
    assert r["status"] == "error"
    assert "credentials" in r["error"].lower()


# ─────────────── recipient regex — header-injection guard ─────────────

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


# ─────────────── CRLF guard on subject + from_name ───────────────────

def test_subject_crlf_rejected():
    s = _configured_sender()
    for evil in ["Subject\r\nBcc: attacker@evil.com", "Subject\nX-Injected: 1", "Subject\rmore"]:
        r = _run(s.send("ok@example.com", evil, "Body"))
        assert r["status"] == "error"
        assert "CRLF" in r["error"]


def test_from_name_crlf_rejected():
    s = _configured_sender()
    r = _run(s.send("ok@example.com", "Clean Subject", "Body",
                     from_name="Evil\r\nBcc: attacker@evil.com"))
    assert r["status"] == "error"
    assert "CRLF" in r["error"]


# ─────────────── positive path — clean inputs reach SMTP ─────────────

def test_clean_inputs_pass_validation_and_reach_send():
    """A well-formed recipient + clean subject/from_name must clear
    every guard and proceed to the SMTP send (mocked here — no I/O)."""
    s = _configured_sender()

    async def _noop_rate_limit():
        return None

    with patch.object(s, "_wait_for_rate_limit", _noop_rate_limit), \
         patch.object(s, "_send_smtp", lambda msg, to: None):
        r = _run(s.send("lead@example.com", "Quick question", "Hello there"))
    assert r["status"] == "sent"
    assert r["to"] == "lead@example.com"
