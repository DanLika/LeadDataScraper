import asyncio
import re
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Optional
from datetime import datetime, timezone

import aiohttp  # type: ignore[import-not-found]


RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 30


class EmailSenderBase(ABC):
    """Abstract base class for email sending implementations."""

    @abstractmethod
    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Send an email and return status dict.

        ``idempotency_key`` is honoured by provider transports that
        support it (Resend HTTP API). SMTP transports ignore it — no
        server-side dedup is available, so the caller must own retry
        semantics.
        """
        pass


class EmailDispatcher(EmailSenderBase):
    """Marker base for senders that participate in the dispatch loop.

    Carries provider metadata so the dispatch orchestrator (Phase
    14.0+, ``docs/email-dispatch-architecture.md`` §0.3) can:

    - Write the right ``provider`` value into ``email_send_ledger``.
    - Skip webhook-driven state transitions when the provider doesn't
      ship them (SMTP).
    - Skip ``Idempotency-Key`` plumbing when the provider doesn't
      honour it.

    Subclasses MUST override ``PROVIDER_NAME`` with one of the values
    in ``email_send_ledger_provider_allowed`` (``'resend'``,
    ``'instantly'``, ``'smtp'`` — see PR ``feature/email-schema-pr2``
    and the multi-dispatcher pivot doc).
    """

    PROVIDER_NAME: ClassVar[str] = ""
    SUPPORTS_WEBHOOKS: ClassVar[bool] = False
    SUPPORTS_IDEMPOTENCY: ClassVar[bool] = False


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
        self._send_times: list = []
        self._lock = asyncio.Lock()

        # Bounce tracking
        self.bounced_emails: set = set()

    async def _wait_for_rate_limit(self):
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

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Send an email via SMTP with rate limiting.

        ``idempotency_key`` is accepted for interface uniformity with
        ResendEmailSender but ignored — SMTP has no equivalent of the
        provider-side Idempotency-Key header.
        """
        del idempotency_key  # interface uniformity only

        if not self.smtp_user or not self.smtp_pass:
            return {"status": "error", "error": "SMTP credentials not configured."}

        # `[^@\s]` excludes whitespace so `\r`/`\n` can't slip through and
        # let an attacker-controlled lead email inject Cc/Bcc/Subject
        # headers via `victim@x.com\r\nBcc: attacker@evil.com`. Recipient
        # passes through `msg["To"] = to` and SMTP RCPT verbatim, so the
        # boundary check has to be strict.
        #
        # `\Z` (not `$`) anchors the end strictly. `$` in Python's `re`
        # also matches BEFORE a trailing `\n`, so `victim@x.com\n` would
        # otherwise smuggle CR/LF into the RCPT envelope. Locked in by
        # `tests/test_crlf_injection.py`.
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z", to):
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
            if any(ch in header_value for ch in ("\r", "\n")):
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
                loop.run_in_executor(None, self._send_smtp, msg, to), timeout=30
            )

            return {
                "status": "sent",
                "to": to,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
        except smtplib.SMTPRecipientsRefused:
            self.bounced_emails.add(to)
            return {"status": "bounced", "error": f"Recipient refused: {to}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _send_smtp(self, msg: MIMEMultipart, to: str):
        """Synchronous SMTP send (run in executor)."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.from_email, to, msg.as_string())


class ResendEmailSender(EmailDispatcher):
    """Resend HTTP API client. Honours ``Idempotency-Key`` so retries
    dedupe at the provider for 24h. Hardening mirrors SMTPEmailSender —
    same ``\\Z``-anchored recipient regex, same CRLF reject on header
    values (subject, from_name, reply_to), same per-instance rate limit
    + short-lived bounce set.

    Scope — **warm / transactional only.** Resend's Acceptable Use
    Policy (https://resend.com/legal/acceptable-use-policy) forbids
    cold outreach to unverified prospects. Cold sends go through
    Instantly's cold-sender pool (Phase 14.x); LinkedIn through
    HeyReach (Phase 17.x). See the multi-dispatcher pivot in
    ``docs/email-dispatch-architecture.md`` §0.

    Suppression list, ``provider_message_id`` persistence, and the
    webhook that drives ``campaign_messages.status`` ship in follow-up
    PRs (see ``docs/email-dispatch-architecture.md`` §0.3 Phase
    13.5c–e).
    """

    PROVIDER_NAME = "resend"
    SUPPORTS_WEBHOOKS = True
    SUPPORTS_IDEMPOTENCY = True

    def __init__(self) -> None:
        self.api_key = os.environ.get("RESEND_API_KEY", "")
        self.from_email = os.environ.get("RESEND_FROM_EMAIL", "")
        self.from_name = os.environ.get("RESEND_FROM_NAME", "LeadDataScraper")
        self.reply_to = os.environ.get("EMAIL_REPLY_TO", "")
        self.list_unsubscribe = os.environ.get("EMAIL_LIST_UNSUBSCRIBE", "")

        self.rate_limit = int(os.environ.get("EMAIL_RATE_LIMIT", "10"))
        self._send_times: list[float] = []
        self._lock = asyncio.Lock()

        self.bounced_emails: set[str] = set()

    async def _wait_for_rate_limit(self) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            self._send_times = [t for t in self._send_times if now - t < 60]

            if len(self._send_times) >= self.rate_limit:
                wait_time = 60 - (now - self._send_times[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self._send_times.append(now)

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST to Resend ``/emails`` endpoint and map the response."""
        if not self.api_key or not self.from_email:
            return {"status": "error", "error": "Resend credentials not configured."}

        # Same `\Z`-anchored recipient regex as SMTP — `\s` excludes
        # CR/LF/VT/FF so attacker-controlled lead emails can't smuggle
        # additional recipients via the `to` field. Resend's API accepts
        # `to` as an array; this regex pins one address per call.
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z", to):
            return {"status": "error", "error": "Invalid email format."}

        if to in self.bounced_emails:
            return {"status": "bounced", "error": f"{to} is in bounce list."}

        # CRLF reject on header-bound values. `reply_to` is operator-set
        # via env today, but a future per-campaign override could route
        # lead data through it — guard now so the boundary doesn't move
        # when that lands.
        sender_name = from_name or self.from_name
        for header_value in (subject, sender_name, self.reply_to):
            if header_value and any(ch in header_value for ch in ("\r", "\n")):
                return {"status": "error", "error": "Invalid header value (CRLF)."}

        await self._wait_for_rate_limit()

        payload: dict[str, Any] = {
            "from": f"{sender_name} <{self.from_email}>",
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if self.reply_to:
            payload["reply_to"] = self.reply_to

        extra_headers: dict[str, str] = {}
        if self.list_unsubscribe:
            extra_headers["List-Unsubscribe"] = self.list_unsubscribe
            extra_headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        if extra_headers:
            payload["headers"] = extra_headers

        request_headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            request_headers["Idempotency-Key"] = idempotency_key

        timeout = aiohttp.ClientTimeout(total=RESEND_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    RESEND_API_URL,
                    headers=request_headers,
                    json=payload,
                ) as resp:
                    status_code = resp.status
                    try:
                        data = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        data = {}
                    return self._map_response(status_code, data, to)
        except asyncio.TimeoutError:
            return {"status": "error", "error": "Email provider timeout."}
        except aiohttp.ClientError:
            return {
                "status": "error",
                "error": "Network failure contacting email provider.",
            }

    def _map_response(
        self, status_code: int, data: dict[str, Any], to: str
    ) -> dict[str, Any]:
        # Error strings never echo `data` payload directly except the
        # 422 `message`. Resend's 422 message is operator-config-driven
        # ("domain not verified", "invalid 'from' value") — the
        # recipient regex above already rejects malformed `to`, so 422
        # in normal flow is an env-config bug not an injection vector.
        if 200 <= status_code < 300:
            provider_id = data.get("id") if isinstance(data, dict) else None
            return {
                "status": "sent",
                "to": to,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "provider_message_id": provider_id,
            }
        if status_code == 401:
            return {"status": "error", "error": "Email provider authentication failed."}
        if status_code == 422:
            msg = data.get("message", "") if isinstance(data, dict) else ""
            return {"status": "error", "error": f"Validation: {msg or 'unknown'}"}
        if status_code == 429:
            return {"status": "rate_limited", "error": "Email provider rate limit."}
        if 500 <= status_code < 600:
            return {
                "status": "error",
                "error": f"Email provider error ({status_code}).",
            }
        return {
            "status": "error",
            "error": f"Unexpected provider response ({status_code}).",
        }


def get_email_sender() -> EmailSenderBase:
    """Factory: pick the email transport via ``EMAIL_PROVIDER`` env.

    Values: ``smtp`` (default) | ``resend_api``. Unknown values raise so
    a typo in env doesn't silently fall back to a transport the operator
    didn't pick.
    """
    provider = os.environ.get("EMAIL_PROVIDER", "smtp").lower().strip()
    if provider == "resend_api":
        return ResendEmailSender()
    if provider in ("smtp", ""):
        return SMTPEmailSender()
    raise ValueError(
        f"Unknown EMAIL_PROVIDER={provider!r}. Use 'smtp' or 'resend_api'."
    )
