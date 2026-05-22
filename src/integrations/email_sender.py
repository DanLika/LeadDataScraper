import asyncio
import re
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from abc import ABC, abstractmethod
from typing import Optional, TypedDict
from datetime import datetime, timezone


class EmailSendResult(TypedDict, total=False):
    """Return shape from `EmailSenderBase.send`.

    Always carries `status` ("sent" | "error" | "bounced"); the remaining
    keys are populated depending on the outcome. `total=False` so handlers
    can produce a partial dict without `pyright`/`mypy` reading absent
    keys as type errors.
    """
    status: str
    to: str
    sent_at: str
    error: str


class EmailSenderBase(ABC):
    """Abstract base class for email sending implementations."""

    @abstractmethod
    async def send(self, to: str, subject: str, body: str, from_name: Optional[str] = None) -> EmailSendResult:
        """Send an email and return status dict."""
        pass


class SMTPEmailSender(EmailSenderBase):
    """SMTP-based email sender with rate limiting and bounce tracking."""

    def __init__(self) -> None:
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_pass = os.environ.get("SMTP_PASS", "")
        self.from_email = os.environ.get("SMTP_FROM", self.smtp_user)
        self.from_name = os.environ.get("SMTP_FROM_NAME", "LeadDataScraper")

        # Rate limiting: max emails per minute
        self.rate_limit = int(os.environ.get("EMAIL_RATE_LIMIT", "10"))
        self._send_times: list[float] = []
        self._lock = asyncio.Lock()

        # Bounce tracking
        self.bounced_emails: set[str] = set()

    async def _wait_for_rate_limit(self) -> None:
        """Enforce rate limiting by waiting if necessary."""
        async with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            # Remove sends older than 60 seconds
            self._send_times = [t for t in self._send_times if now - t < 60]

            if len(self._send_times) >= self.rate_limit:
                wait_time = 60 - (now - self._send_times[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self._send_times.append(now)

    async def send(self, to: str, subject: str, body: str, from_name: Optional[str] = None) -> EmailSendResult:
        """Send an email via SMTP with rate limiting."""
        if not self.smtp_user or not self.smtp_pass:
            return {"status": "error", "error": "SMTP credentials not configured."}

        # `[^@\s]` excludes whitespace so `\r`/`\n` can't slip through and
        # let an attacker-controlled lead email inject Cc/Bcc/Subject
        # headers via `victim@x.com\r\nBcc: attacker@evil.com`. Recipient
        # passes through `msg["To"] = to` and SMTP RCPT verbatim, so the
        # boundary check has to be strict.
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', to):
            return {"status": "error", "error": "Invalid email format."}

        if to in self.bounced_emails:
            return {"status": "bounced", "error": f"{to} is in bounce list."}

        # CRLF guard on every header value we feed MIMEMultipart. Subject
        # is Gemini-drafted (sees attacker-controlled lead fields) and
        # from_name can also be overridden per-send. Header injection at
        # this layer would let a single poisoned lead row alter every
        # subsequent recipient on the message.
        sender_name = from_name or self.from_name
        for header_value in (subject, sender_name):
            if any(ch in header_value for ch in ('\r', '\n')):
                return {"status": "error", "error": "Invalid header value (CRLF)."}

        await self._wait_for_rate_limit()

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{sender_name} <{self.from_email}>"
            msg["To"] = to

            # Plain text version
            msg.attach(MIMEText(body, "plain"))

            # Send in executor with timeout to avoid blocking indefinitely
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self._send_smtp, msg, to),
                timeout=30
            )

            return {"status": "sent", "to": to, "sent_at": datetime.now(timezone.utc).isoformat()}
        except smtplib.SMTPRecipientsRefused:
            self.bounced_emails.add(to)
            return {"status": "bounced", "error": f"Recipient refused: {to}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _send_smtp(self, msg: MIMEMultipart, to: str) -> None:
        """Synchronous SMTP send (run in executor)."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.from_email, to, msg.as_string())
