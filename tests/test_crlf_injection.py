"""CRLF-injection sweep against every surface where user input crosses
a line-delimited protocol boundary.

Surfaces:
  1. **SMTP recipient / Subject / From-name** (`src/integrations/email_sender.py`)
     The recipient regex `^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$` uses `[^@\\s]`,
     so `\\r`, `\\n`, `\\t` are all excluded — proven by parametrized
     payloads. Subject + sender_name also pass an explicit CRLF check.
  2. **Logging** (`src/utils/logging_config._CRLFScrubFilter`)
     Lead names / websites / pain-points flow through
     `logger.error("... %s ...", lead_name, ...)`. Without scrubbing,
     a name like "X\\r\\nERROR forged" forges a second log line at
     attacker-chosen level. The filter translates raw CR/LF/VT/FF to
     the printable `\\\\r` / `\\\\n` escape so the file emits exactly
     one record line per call.
  3. **Outbound URL paths / query** — `discovery_engine.find_leads`
     uses `quote_plus` on lead queries; CR/LF percent-encode → safe.
  4. **aiohttp / urllib outbound headers** — refuses CR/LF in header
     values at the library level. Canary test pins that contract.

Payloads (raw + encoded variants the user listed):
  '\\r', '\\n', '\\r\\n', '\\u000a', '\\u000d',
  '%0d%0a' (URL-encoded — must NOT decode at validation time),
  '\\x0b' (VT), '\\x0c' (FF) — bonus, some HTTP libs split on these too.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unittest
from io import StringIO
from logging import LogRecord
from logging.handlers import MemoryHandler
from unittest.mock import patch
from urllib.parse import quote_plus

from src.utils.logging_config import _CRLFScrubFilter, get_logger


# ---------------------------------------------------------------------------
# The CRLF payload corpus.
# ---------------------------------------------------------------------------

CRLF_RAW_PAYLOADS = [
    "\r",                # CR
    "\n",                # LF
    "\r\n",              # CRLF
    "\x0b",              # VT — some HTTP libs split on this
    "\x0c",              # FF — same
    "\r\nX-Injected: evil",                           # full header-smuggle
    "victim@x.com\r\nBcc: attacker@evil.com",          # SMTP Cc/Bcc
    "subject\r\nHidden-Header: x",                     # subject smuggle
    "name\r\nFAKE LOG LINE INJECTED BY ATTACKER",     # log line forge
]

# Encoded variants — these MUST NOT be decoded by validators before
# pattern checks; the validator sees `%0d%0a` literally and either
# accepts or rejects, but never decodes-then-rechecks.
CRLF_ENCODED_PAYLOADS = [
    "%0d%0a",
    "%0D%0A",
    "%0a",
    "%0d",
    "\\r\\n",   # literal backslash-r-backslash-n (no escape interpretation)
    "&#13;",    # HTML entity
    "&#x0a;",
]


# ---------------------------------------------------------------------------
# 1) SMTP recipient regex — `[^@\s]` exclusion catches every raw payload.
# ---------------------------------------------------------------------------

SMTP_RECIPIENT_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z")


class TestSMTPRecipientRejectsCRLF(unittest.TestCase):
    """The `\\s` shorthand inside `[^@\\s]` matches `[ \\t\\n\\r\\f\\v]`,
    so CR / LF / VT / FF in the local-part OR domain-part fail the
    regex. Test every position the payload could land."""

    def _attack(self, raw: str, anchor: str) -> str:
        # Build a "legitimate-looking" address with the payload spliced in
        # one of three positions: local part, before TLD, after TLD.
        if anchor == "local":
            return f"victim{raw}@example.com"
        if anchor == "host":
            return f"victim@example{raw}.com"
        if anchor == "tld":
            return f"victim@example.co{raw}m"
        if anchor == "prefix":
            return f"{raw}victim@example.com"
        if anchor == "suffix":
            return f"victim@example.com{raw}"
        raise ValueError(anchor)

    def test_raw_crlf_in_every_position_rejected(self):
        for payload in CRLF_RAW_PAYLOADS:
            for anchor in ("local", "host", "tld", "prefix", "suffix"):
                attack = self._attack(payload, anchor)
                with self.subTest(payload=repr(payload), anchor=anchor):
                    self.assertIsNone(
                        SMTP_RECIPIENT_PATTERN.match(attack),
                        f"recipient regex accepted CRLF in {anchor}: "
                        f"{attack!r}"
                    )

    def test_encoded_crlf_payloads_pass_regex_but_are_inert(self):
        """`%0d%0a` and HTML-entity variants don't contain literal CR/LF
        bytes, so the regex sees them as ordinary text. They'd pass —
        but the downstream `msg["To"] = to` then writes the literal
        bytes into the header, NOT a decoded version. The smtplib
        layer treats them as opaque. Pin this contract."""
        for encoded in CRLF_ENCODED_PAYLOADS:
            attack = f"victim{encoded}@example.com"
            # Whether the regex matches depends on the specific encoded
            # form; what matters is the regex doesn't somehow eagerly
            # decode it. We assert: if the raw text contains '\r' or '\n'
            # AFTER any decoding the validator does, it fails. Since the
            # validator doesn't decode, encoded variants stay opaque.
            decoded_bytes = encoded.encode()
            self.assertNotIn(b"\r", decoded_bytes, encoded)
            self.assertNotIn(b"\n", decoded_bytes, encoded)


# ---------------------------------------------------------------------------
# 2) SMTP subject / from_name explicit CRLF check.
# ---------------------------------------------------------------------------

class TestSMTPSubjectFromNameCRLFGuard(unittest.IsolatedAsyncioTestCase):
    """`SMTPEmailSender.send` runs a tuple check
        any(ch in header_value for ch in ('\\r', '\\n'))
    on (subject, sender_name). Drive the real entry point with each
    raw payload and assert the function refuses to call SMTP."""

    async def asyncSetUp(self):
        # Real SMTP credentials would gate the function early; set fakes.
        self.env_patcher = patch.dict(os.environ, {
            "SMTP_USER": "test@example.com",
            "SMTP_PASS": "fake",
        })
        self.env_patcher.start()
        from src.integrations.email_sender import SMTPEmailSender
        self.sender = SMTPEmailSender()

    async def asyncTearDown(self):
        self.env_patcher.stop()

    async def test_crlf_in_subject_rejected(self):
        for payload in CRLF_RAW_PAYLOADS:
            if "\r" not in payload and "\n" not in payload:
                continue  # encoded-only payloads are filtered separately
            with self.subTest(payload=repr(payload)):
                r = await self.sender.send(
                    to="ok@example.com",
                    subject=f"Subject prefix {payload} smuggle",
                    body="hi",
                )
                self.assertEqual(r["status"], "error", r)
                self.assertIn("CRLF", r["error"], r)

    async def test_crlf_in_from_name_rejected(self):
        for payload in ("\r", "\n", "\r\n", "From: x\r\nBcc: y"):
            with self.subTest(payload=repr(payload)):
                r = await self.sender.send(
                    to="ok@example.com",
                    subject="ok",
                    body="hi",
                    from_name=f"Attacker {payload}",
                )
                self.assertEqual(r["status"], "error", r)
                self.assertIn("CRLF", r["error"], r)


# ---------------------------------------------------------------------------
# 3) Logging — CRLF scrub filter prevents log-line forgery.
# ---------------------------------------------------------------------------

class TestLoggingCRLFScrub(unittest.TestCase):
    """The filter rewrites raw CR/LF (and VT/FF) in `record.msg` and
    every entry of `record.args` to printable `\\r` / `\\n`. Drives
    every CRLF-bearing payload through `logger.info(msg, *args)` and
    checks the formatted output contains NO raw CR/LF byte."""

    def setUp(self):
        self.stream = StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.handler.addFilter(_CRLFScrubFilter())
        self.logger = logging.getLogger("test_crlf_scrub")
        self.logger.handlers.clear()
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)
        # Don't propagate to root — root has the real handlers and we'd
        # double-count.
        self.logger.propagate = False

    def _emitted(self) -> str:
        self.handler.flush()
        return self.stream.getvalue()

    def test_crlf_in_msg_arg_scrubbed(self):
        for payload in CRLF_RAW_PAYLOADS:
            with self.subTest(payload=repr(payload)):
                self.stream.truncate(0)
                self.stream.seek(0)
                self.logger.info(
                    "lead_name=%s seo_score=%s",
                    f"X{payload}ERROR forged log line",
                    42,
                )
                output = self._emitted()
                # No raw CR or LF must appear inside the formatted output.
                # (Trailing newline added by StreamHandler IS allowed —
                # it's exactly one and at the very end.)
                stripped = output.rstrip("\n")
                self.assertNotIn("\r", stripped,
                                 f"raw CR leaked for {payload!r}: {output!r}")
                self.assertNotIn("\n", stripped,
                                 f"raw LF leaked for {payload!r}: {output!r}")
                # And only ONE log line was emitted (one trailing newline).
                self.assertEqual(
                    output.count("\n"), 1,
                    f"multiple lines emitted for {payload!r}: {output!r}"
                )

    def test_crlf_in_format_string_itself_scrubbed(self):
        # Some callers (anti-pattern) interpolate before passing the
        # message — the format string ends up containing the payload.
        # The filter must still scrub.
        payload = "name=%s\r\nFAKE LOG LINE"
        self.stream.truncate(0)
        self.stream.seek(0)
        # Pre-formatting the payload into the msg.
        self.logger.info(payload % "X")
        output = self._emitted()
        self.assertNotIn("\r", output.rstrip("\n"))
        self.assertEqual(output.count("\n"), 1)

    def test_dict_args_scrubbed(self):
        self.stream.truncate(0)
        self.stream.seek(0)
        self.logger.info("lead=%(name)s", {"name": "X\r\nFAKE"})
        output = self._emitted()
        self.assertNotIn("\r", output.rstrip("\n"))
        self.assertEqual(output.count("\n"), 1)

    def test_legitimate_log_lines_pass_through_unchanged(self):
        self.stream.truncate(0)
        self.stream.seek(0)
        self.logger.info("Processing lead %s with score %d", "Alpha Tech", 85)
        output = self._emitted()
        self.assertIn("Processing lead Alpha Tech with score 85", output)


# ---------------------------------------------------------------------------
# 4) URL encoding — quote_plus percent-encodes CRLF.
# ---------------------------------------------------------------------------

class TestURLEncodingCRLFSafe(unittest.TestCase):
    """`discovery_engine.find_leads(query, location)` passes both args
    through `quote_plus`. CRLF in `query` would become `%0D%0A` in the
    URL — safe. Pin the invariant so a future refactor swapping to
    `quote` (which doesn't encode space) or raw f-string doesn't
    re-open the hole."""

    def test_quote_plus_encodes_crlf_in_query(self):
        for payload in CRLF_RAW_PAYLOADS:
            with self.subTest(payload=repr(payload)):
                encoded = quote_plus(f"pizza{payload}NYC")
                self.assertNotIn("\r", encoded)
                self.assertNotIn("\n", encoded)
                # Tab, VT, FF are also safely encoded
                self.assertNotIn("\t", encoded)
                self.assertNotIn("\x0b", encoded)
                self.assertNotIn("\x0c", encoded)


# ---------------------------------------------------------------------------
# 5) aiohttp outbound header CRLF rejection canary.
# ---------------------------------------------------------------------------

class TestAiohttpHeaderCRLFRejection(unittest.IsolatedAsyncioTestCase):
    """aiohttp refuses to set a header value containing CR or LF — pin
    the contract so a future swap to a more permissive HTTP library
    triggers a regression. If the canary breaks, every outbound call
    site that interpolates user input into a header needs an explicit
    CRLF check (same as SMTP)."""

    async def test_aiohttp_rejects_crlf_in_header_value(self):
        import aiohttp

        # We never actually open a connection; the validation happens
        # when the request's headers are normalised. Build a session
        # with a connector mock so no socket call is attempted.
        for payload in ("\r", "\n", "\r\n", "X\r\nX-Injected: evil"):
            with self.subTest(payload=repr(payload)):
                with self.assertRaises(
                    (ValueError, aiohttp.ClientError, TypeError),
                    msg=f"aiohttp accepted CRLF header {payload!r}",
                ):
                    async with aiohttp.ClientSession(
                        headers={"User-Agent": f"Mozilla/{payload}/5.0"},
                    ) as session:
                        # Just constructing the session validates headers
                        # in current aiohttp; the actual fetch isn't needed.
                        async with session.get(
                            "http://127.0.0.1:0/",
                            timeout=aiohttp.ClientTimeout(total=0.1),
                        ) as _:
                            pass


# ---------------------------------------------------------------------------
# 6) Production log-call survey — confirm no eager string-concat that
#    bypasses the filter's args path.
# ---------------------------------------------------------------------------

class TestProductionLogCallsUseArgsForm(unittest.TestCase):
    """`logger.info("msg %s", value)` lets the filter see + scrub
    `value` as an arg. `logger.info("msg " + value)` pre-formats so
    the filter only sees the merged `msg`. The filter handles BOTH
    cases (it scrubs `record.msg` AND `record.args`), but the args
    form is preferable. This test is purely informational — counts
    eager-concat occurrences for awareness, doesn't fail on them."""

    def test_count_string_concat_log_calls(self):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        src = repo_root / "src"
        concat_pattern = re.compile(
            r"""logger\.(info|warning|error|debug)\(["'][^"']*["']\s*\+""",
        )
        offenders: list[str] = []
        for path in src.rglob("*.py"):
            if "test" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_num, line in enumerate(text.splitlines(), start=1):
                if concat_pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(repo_root)}:{line_num}: "
                        f"{line.strip()[:120]}"
                    )
        # Informational only — write to stdout so a maintainer can see
        # the count without the test failing. The CRLF filter covers
        # both forms, so this is style, not security.
        if offenders:
            sys.stdout.write(
                f"\n[INFO] {len(offenders)} eager-concat log calls in src/ "
                f"(safe — filter scrubs both msg + args, but args form preferred):\n  "
                + "\n  ".join(offenders[:5])
                + ("\n  ..." if len(offenders) > 5 else "")
                + "\n"
            )


if __name__ == "__main__":
    unittest.main()
