"""HTTP response body cap on the SEO auditor (vibe-security M6).

The auditor's `aiohttp.ClientSession.get(...)` previously called
`response.text()` against attacker-controlled lead websites. The 20s
wall-clock timeout (`aiohttp.ClientTimeout(total=20)`) does NOT bound
bytes — a slow-trickle malicious server can stream 100 MB at
just-under-timeout throughput and balloon the worker's memory before
the timeout fires. The downstream `html[:50_000]` slice in
`_extract_emails_and_text` happens AFTER the full body is in RAM, so
it does NOT protect the worker.

This test file pins:

- The 2 MB cap (`MAX_HTML_BYTES`) — bodies at or below succeed; bodies
  one byte over reject with `AuditFetchError`.
- The warning log emitted on overshoot (so an operator sees the signal
  even if the orchestrator catches the exception generically).
- Empty body succeeds (no off-by-one at the lower boundary).
- Malformed UTF-8 decodes with `errors="replace"` rather than crashing
  the auditor with `UnicodeDecodeError`.

Mocking strategy: we patch `aiohttp.ClientSession.get` at the seo_audit
module boundary to return a stub response whose `content.read(n)`
returns a pre-built bytes payload truncated at `n`. The SSRF resolver
and DNS path are bypassed because no socket call is made.
"""

import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.errors import AuditFetchError
from src.scrapers.seo_audit import MAX_HTML_BYTES, perform_seo_audit_async


class _StubResponse:
    """Stub for the aiohttp response object returned by session.get().

    Implements just the surface seo_audit reads:
    - `content.read(n)` — awaitable returning bytes up to `n`
    - `charset` — string or None
    - async context-manager protocol (`__aenter__` / `__aexit__`)
    """

    def __init__(self, body: bytes, charset: str | None = "utf-8"):
        self._body = body
        self.charset = charset
        self.content = MagicMock()
        # The cap branch reads MAX_HTML_BYTES + 1, so emulate the bytes
        # protocol: return at most `n` bytes from the front of the buffer.
        self.content.read = AsyncMock(side_effect=self._read)

    async def _read(self, n: int) -> bytes:
        return self._body[:n]

    async def __aenter__(self) -> "_StubResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _StubSession:
    """Stub for aiohttp.ClientSession. Returns a pre-built response on get().

    Async context-manager. Implements only what perform_seo_audit_async uses.
    """

    def __init__(self, response: _StubResponse):
        self._response = response

    def get(self, url, **kwargs):
        return self._response

    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _patch_session(response: _StubResponse):
    """Patch the auditor's `aiohttp.ClientSession` constructor to return
    a stub session. Also patches `aiohttp.TCPConnector` because the
    auditor builds one with `SSRFGuardResolver` — we don't want that
    DNS path to fire under test."""
    return patch.multiple(
        "src.scrapers.seo_audit.aiohttp",
        ClientSession=MagicMock(return_value=_StubSession(response)),
        TCPConnector=MagicMock(return_value=MagicMock()),
    )


@pytest.mark.security
@pytest.mark.asyncio
class TestSeoAuditBodyCap(unittest.IsolatedAsyncioTestCase):
    """Lock in the 2 MB HTTP response body cap. Locks the module-level
    constant + the AuditFetchError propagation contract."""

    # ----- 6 cases, ordered from below-cap to over-cap then edge cases.

    async def test_body_just_under_cap_succeeds(self):
        """500 KB body — well under the 2 MB cap — populates html and
        sets is_up=True. No exception."""
        body = b"<html><body>" + b"x" * (500 * 1024) + b"</body></html>"
        self.assertLess(len(body), MAX_HTML_BYTES)
        with _patch_session(_StubResponse(body)):
            result = await perform_seo_audit_async("https://safe.example.com")
        self.assertTrue(result["is_up"])
        # Result should not carry an "exceeds cap" red_flag.
        for flag in result.get("red_flags", []):
            self.assertNotIn("exceeds", str(flag).lower())

    async def test_body_exactly_at_cap_succeeds(self):
        """2_097_152 bytes — exactly at the cap — must NOT raise.
        The check is `len(raw) > MAX_HTML_BYTES`, strict inequality."""
        body = b"a" * MAX_HTML_BYTES
        self.assertEqual(len(body), MAX_HTML_BYTES)
        with _patch_session(_StubResponse(body)):
            result = await perform_seo_audit_async("https://atcap.example.com")
        self.assertTrue(result["is_up"])

    async def test_body_one_byte_over_cap_raises(self):
        """2_097_153 bytes — one byte over — must raise AuditFetchError."""
        body = b"a" * (MAX_HTML_BYTES + 1)
        self.assertGreater(len(body), MAX_HTML_BYTES)
        with _patch_session(_StubResponse(body)):
            with self.assertRaises(AuditFetchError):
                await perform_seo_audit_async("https://overcap.example.com")

    async def test_body_well_over_cap_raises_and_logs_warning(self):
        """3 MB body — must raise AuditFetchError AND emit a WARNING log
        line carrying the URL + byte count. The orchestrator's per-lead
        handler will see the exception; the warn is for the operator to
        notice the pattern."""
        body = b"a" * (3 * 1024 * 1024)
        with _patch_session(_StubResponse(body)):
            with self.assertLogs("src.scrapers.seo_audit", level="WARNING") as cm:
                with self.assertRaises(AuditFetchError):
                    await perform_seo_audit_async("https://huge.example.com")
        warn_text = "\n".join(cm.output)
        self.assertIn("body exceeds cap", warn_text)
        self.assertIn("huge.example.com", warn_text)

    async def test_empty_body_succeeds(self):
        """Empty response body — must NOT raise. is_up should still be
        True because the HTTP fetch completed; downstream parsers handle
        the empty-html case (BeautifulSoup parses an empty doc fine)."""
        with _patch_session(_StubResponse(b"")):
            result = await perform_seo_audit_async("https://empty.example.com")
        self.assertTrue(result["is_up"])

    async def test_body_with_mojibake_decodes_replacement(self):
        """Malformed UTF-8 bytes must decode without raising
        UnicodeDecodeError — `errors="replace"` substitutes the
        replacement char so the auditor stays robust against servers
        sending lying Content-Type headers or corrupt payloads."""
        # 0xFF 0xFE 0x00 0x01 is not valid UTF-8.
        body = b"\xff\xfe\x00\x01<html><body>ok</body></html>"
        # Pretend the server claims utf-8 — common attack shape.
        with _patch_session(_StubResponse(body, charset="utf-8")):
            result = await perform_seo_audit_async("https://mojibake.example.com")
        # No exception means the decode succeeded; is_up True confirms
        # we reached the post-fetch path.
        self.assertTrue(result["is_up"])


if __name__ == "__main__":
    unittest.main()
