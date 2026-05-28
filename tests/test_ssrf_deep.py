"""Deep SSRF tests beyond `tests/test_ssrf_guard.py`.

Covers the failure modes that don't show up in the simple "is this IP
public?" check:

  1. DNS rebinding — resolver returns a public IP on the first call
     (pre-flight `assert_safe_url`) and a loopback IP on the second
     (connection-time `SSRFGuardResolver.resolve` via aiohttp's
     `TCPConnector`). The connection-time check MUST catch the rebind.
  2. IPv4-mapped IPv6 (`::ffff:<IPv4>`) — `is_global` correctly reports
     False on Python 3.10+ because the IPv4 classification propagates
     into the v6 wrapper. We test the invariant directly so a future
     CPython change or a guard rewrite that strips the v6 wrapper
     can't silently re-open the loopback / link-local / metadata path.
  3. Pure IPv6 internal ranges — `::1`, `fe80::/10`, `fc00::/7`.
  4. Redirect chain to an internal IP — verifies that
     `SSRFGuardResolver` raises even when the IP literal short-circuits
     real DNS (aiohttp's `DefaultResolver` returns synthetic
     `AI_NUMERICHOST` results for raw IPs; the subclass iterates and
     re-checks).
  5. Bounded redirect chain — production aiohttp uses the default
     `max_redirects=10`. A 50-hop chain raises `TooManyRedirects`
     rather than hanging.
  6. HTTP/0.9 smuggling — aiohttp's parser refuses HTTP/0.9 entirely.
     Documented here as a coupling-test reminder; if the upstream
     parser ever relaxes that, we want noise.
  7. SNI / Host-header confusion — no production aiohttp call site
     overrides the `Host` header on SSRF-sensitive fetches, so the
     SNI host (taken from the URL) and the HTTP `Host` header always
     match.
  8. TXT-record exfil — not a SSRF vector against our HTTP guard
     (DNS TXT queries are issued by the resolver only on `MX`/`TXT`
     lookups, and our code never asks for either). Asserted via
     static grep so a future "lookup TXT for SPF" path can't
     accidentally land without a guard.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.ssrf_guard import (
    SSRFError,
    SSRFGuardResolver,
    _assert_public_ip,
    assert_safe_scheme,
    assert_safe_url,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "backend"]


# ---------------------------------------------------------------------------
# 1) DNS rebinding — connection-time check sees the rebound private IP.
# ---------------------------------------------------------------------------


class TestDnsRebinding(unittest.IsolatedAsyncioTestCase):
    """Two consecutive resolve() calls return different IPs. Pre-flight
    sees a public address; the second (made by aiohttp inside the
    TCPConnector at connection time) sees `127.0.0.1`. The resolver must
    reject the second call — closing the TOCTOU window between the
    `assert_safe_url` check and the actual fetch."""

    async def test_second_resolve_rejects_rebound_loopback(self):
        # Sequenced results: first call → public, second call → loopback.
        rebind_sequence = [
            [
                {
                    "host": "8.8.8.8",
                    "port": 80,
                    "family": socket.AF_INET,
                    "hostname": "rebind.example",
                    "flags": 0,
                    "proto": 6,
                }
            ],
            [
                {
                    "host": "127.0.0.1",
                    "port": 80,
                    "family": socket.AF_INET,
                    "hostname": "rebind.example",
                    "flags": 0,
                    "proto": 6,
                }
            ],
        ]
        super_resolve = AsyncMock(side_effect=rebind_sequence)

        resolver = SSRFGuardResolver()
        with patch(
            "aiohttp.resolver.DefaultResolver.resolve",
            new=super_resolve,
        ):
            # First resolve: passes (public IP).
            await resolver.resolve("rebind.example", 80)
            # Second resolve: rebound → loopback → must raise.
            with self.assertRaises(SSRFError):
                await resolver.resolve("rebind.example", 80)

    async def test_rebind_to_metadata_ip_rejected(self):
        """Pre-flight passes; rebind points the same hostname at the EC2
        metadata IP. Connection-time resolver must raise."""
        rebind_sequence = [
            [
                {
                    "host": "104.21.0.1",
                    "port": 80,
                    "family": socket.AF_INET,
                    "hostname": "rebind.example",
                    "flags": 0,
                    "proto": 6,
                }
            ],
            [
                {
                    "host": "169.254.169.254",
                    "port": 80,
                    "family": socket.AF_INET,
                    "hostname": "rebind.example",
                    "flags": 0,
                    "proto": 6,
                }
            ],
        ]
        super_resolve = AsyncMock(side_effect=rebind_sequence)

        resolver = SSRFGuardResolver()
        with patch(
            "aiohttp.resolver.DefaultResolver.resolve",
            new=super_resolve,
        ):
            await resolver.resolve("rebind.example", 80)
            with self.assertRaises(SSRFError) as ctx:
                await resolver.resolve("rebind.example", 80)
            self.assertIn("169.254.169.254", str(ctx.exception))


# ---------------------------------------------------------------------------
# 2 + 3) IPv6: IPv4-mapped + pure IPv6 internal ranges.
# ---------------------------------------------------------------------------


class TestIPv6Classification(unittest.TestCase):
    """Python's `ipaddress.IPv6Address.is_global` correctly returns False
    for `::ffff:<IPv4>` when the wrapped IPv4 is non-public, because the
    classification propagates into the v6 view. This test pins that
    invariant — if a future CPython change or a guard rewrite strips
    the v4-mapped check, the loopback / link-local / private path
    re-opens silently."""

    def _assert_blocks(self, addr: str):
        ip = ipaddress.ip_address(addr)
        with self.assertRaises(SSRFError, msg=f"{addr} not rejected"):
            _assert_public_ip(ip, addr)

    def test_ipv4_mapped_loopback_rejected(self):
        self._assert_blocks("::ffff:127.0.0.1")

    def test_ipv4_mapped_link_local_metadata_rejected(self):
        self._assert_blocks("::ffff:169.254.169.254")

    def test_ipv4_mapped_private_10_rejected(self):
        self._assert_blocks("::ffff:10.0.0.1")

    def test_ipv4_mapped_private_192_168_rejected(self):
        self._assert_blocks("::ffff:192.168.1.1")

    def test_ipv4_mapped_private_172_16_rejected(self):
        self._assert_blocks("::ffff:172.16.0.1")

    def test_ipv6_loopback_rejected(self):
        self._assert_blocks("::1")

    def test_ipv6_link_local_rejected(self):
        self._assert_blocks("fe80::1")

    def test_ipv6_unique_local_rejected(self):
        self._assert_blocks("fc00::1")
        self._assert_blocks("fd00::1")

    def test_ipv6_unspecified_rejected(self):
        self._assert_blocks("::")

    def test_ipv6_documentation_range_rejected(self):
        # 2001:db8::/32 reserved for documentation — not routable.
        self._assert_blocks("2001:db8::1")

    def test_pure_ipv6_public_address_allowed(self):
        ip = ipaddress.ip_address("2606:4700:4700::1111")  # Cloudflare DNS v6
        try:
            _assert_public_ip(ip, "2606:4700:4700::1111")
        except SSRFError as e:
            self.fail(f"Public IPv6 address rejected: {e}")


class TestAssertSafeSchemeWithIPv6(unittest.TestCase):
    """Same coverage but through the public entry point — catches a
    refactor that adds an `isinstance(IPv6Address)` short-circuit and
    skips classification."""

    def test_assert_safe_scheme_blocks_ipv4_mapped_loopback(self):
        with self.assertRaises(SSRFError):
            assert_safe_scheme("http://[::ffff:127.0.0.1]/x")

    def test_assert_safe_scheme_blocks_ipv6_link_local(self):
        with self.assertRaises(SSRFError):
            assert_safe_scheme("http://[fe80::1]/x")

    def test_assert_safe_scheme_blocks_ipv4_mapped_metadata(self):
        with self.assertRaises(SSRFError):
            assert_safe_scheme("http://[::ffff:169.254.169.254]/")


class TestAssertSafeUrlWithIPv6(unittest.IsolatedAsyncioTestCase):
    async def test_assert_safe_url_blocks_ipv4_mapped_loopback(self):
        with self.assertRaises(SSRFError):
            await assert_safe_url("http://[::ffff:127.0.0.1]/")

    async def test_assert_safe_url_blocks_metadata_v6(self):
        with self.assertRaises(SSRFError):
            await assert_safe_url("http://[fe80::1]/")


# ---------------------------------------------------------------------------
# 4) Redirect-target rejected — resolver still re-checks for IP literals.
# ---------------------------------------------------------------------------


class TestRedirectTargetGuard(unittest.IsolatedAsyncioTestCase):
    """aiohttp's `DefaultResolver.resolve('<ip-literal>')` returns a
    synthetic entry (`AI_NUMERICHOST`) without real DNS. `SSRFGuardResolver`
    must still iterate and raise on private IPs — otherwise a redirect
    from a public host to `http://169.254.169.254/` would bypass the
    guard. We exercise that code path directly so the invariant is
    locked in independent of aiohttp's redirect implementation."""

    async def test_resolver_rejects_ip_literal_metadata(self):
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("169.254.169.254", 80)

    async def test_resolver_rejects_ip_literal_loopback(self):
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("127.0.0.1", 80)

    async def test_resolver_rejects_ipv4_mapped_ipv6_literal(self):
        # aiohttp's resolver normalises bracketed IPv6 to the inner form
        # before calling resolve(host=...). The guard must catch.
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("::ffff:127.0.0.1", 80, family=socket.AF_INET6)


# ---------------------------------------------------------------------------
# 5) Bounded redirect chain — default aiohttp max_redirects=10.
# ---------------------------------------------------------------------------


class TestProductionRedirectsBounded(unittest.TestCase):
    """`seo_audit.py` and `enrichment_engine.py` must not override
    `max_redirects` to a value that would let a 50-hop chain hang the
    worker. We check via static scan: any `session.get(... max_redirects=N)`
    must have N ≤ 10 (aiohttp's safe default)."""

    def test_no_unbounded_max_redirects_override(self):
        offenders: list[str] = []
        pattern = re.compile(
            r"max_redirects\s*=\s*(\d+|None|float\.inf)",
            re.IGNORECASE,
        )
        for root in SRC_ROOTS:
            for path in root.rglob("*.py"):
                if "test" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for line_num, line in enumerate(text.splitlines(), start=1):
                    m = pattern.search(line)
                    if not m:
                        continue
                    val = m.group(1)
                    if val.lower() in ("none", "float.inf"):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{line_num} "
                            f"unbounded max_redirects: {line.strip()}"
                        )
                        continue
                    try:
                        if int(val) > 10:
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{line_num} "
                                f"max_redirects={val} (cap: 10)"
                            )
                    except ValueError:
                        pass
        self.assertEqual(
            offenders,
            [],
            "Unbounded / oversized max_redirects overrides found:\n  "
            + "\n  ".join(offenders),
        )


# ---------------------------------------------------------------------------
# 6) HTTP/0.9 smuggling — aiohttp's parser refuses HTTP/0.9.
# ---------------------------------------------------------------------------


class TestHttp09Refused(unittest.IsolatedAsyncioTestCase):
    """HTTP/0.9 responses have no status line and no headers — body only.
    A header-aware proxy that treats an HTTP/0.9 body as the response
    headers becomes a header-smuggling oracle. aiohttp's parser refuses
    HTTP/0.9 entirely; we run a tiny in-process TCP server that emits
    one and verify aiohttp surfaces a parser error rather than treating
    the body as a successful response."""

    async def test_http09_response_raises_not_silently_accepted(self):
        import aiohttp

        async def http09_responder(reader, writer):
            # Read the request (ignore it) then emit raw bytes — no
            # status line, no headers, no CRLF before body.
            try:
                await reader.read(4096)
            except Exception:
                pass
            writer.write(b"this is a raw http/0.9 body with no headers")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        server = await asyncio.start_server(http09_responder, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                with self.assertRaises(
                    (aiohttp.ClientError, asyncio.TimeoutError),
                    msg="HTTP/0.9 must not silently parse as success",
                ):
                    async with session.get(f"http://127.0.0.1:{port}/") as resp:
                        await resp.text()
        finally:
            server.close()
            await server.wait_closed()


# ---------------------------------------------------------------------------
# 7) SNI / Host-header confusion — no manual Host override on outbound fetches.
# ---------------------------------------------------------------------------


class TestNoManualHostHeaderOverride(unittest.TestCase):
    """If a production aiohttp / Playwright call manually sets the
    `Host` header on a request, the TLS SNI (taken from the URL host)
    and the HTTP `Host` header can disagree — letting a request hit
    one origin while pretending to be addressed at another. Verify
    nobody does that in SSRF-sensitive modules."""

    def test_no_host_header_set_in_ssrf_callers(self):
        target_files = [
            REPO_ROOT / "src" / "scrapers" / "seo_audit.py",
            REPO_ROOT / "src" / "scrapers" / "enrichment_engine.py",
            REPO_ROOT / "src" / "scrapers" / "discovery_engine.py",
        ]
        pattern = re.compile(
            r"""['"]?[Hh]ost['"]?\s*[:=]""",
        )
        offenders: list[str] = []
        for path in target_files:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_num, line in enumerate(text.splitlines(), start=1):
                # Allow comments and references to `hostname` — only flag
                # literal `Host: ...` / `'Host': ...` / `Host = ...` headers.
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "hostname" in stripped.lower():
                    continue
                if not pattern.search(stripped):
                    continue
                # The string must be standalone — context-sensitive false
                # positives ("upstream_host = ..." or "host = parsed.host")
                # are filtered by requiring the matched token to be
                # literally `Host` or `'Host'` / `"Host"` with no preceding
                # word characters.
                strict = re.search(
                    r"""(?<![\w_])['"]?[Hh]ost['"]?\s*[:=]""",
                    stripped,
                )
                if strict:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{line_num} "
                        f"manual Host header set: {stripped[:120]}"
                    )
        self.assertEqual(
            offenders,
            [],
            "Manual Host header overrides — TLS SNI may diverge from "
            "HTTP Host. Found:\n  " + "\n  ".join(offenders),
        )


# ---------------------------------------------------------------------------
# 8) TXT-record exfil — no DNS TXT/MX lookups in SSRF-sensitive code.
# ---------------------------------------------------------------------------


class TestNoTxtRecordExfil(unittest.TestCase):
    """An attacker who can supply a hostname could exfil data through
    DNS TXT/MX queries if our code asks for them. The SSRF guard only
    classifies A/AAAA results; a `dns.resolver.resolve(host, 'TXT')`
    would issue the query regardless. Lock the invariant: no production
    file imports `dnspython` / `dns.resolver` and no code queries
    `'TXT'` / `'MX'` / `'NS'` records."""

    def test_no_dnspython_imports(self):
        offenders: list[str] = []
        forbidden = (
            "import dns.resolver",
            "from dns import resolver",
            "from dns.resolver",
            "import dnspython",
        )
        for root in SRC_ROOTS:
            for path in root.rglob("*.py"):
                if "test" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for line_num, line in enumerate(text.splitlines(), start=1):
                    if line.lstrip().startswith("#"):
                        continue
                    for fragment in forbidden:
                        if fragment in line:
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{line_num} {line.strip()}"
                            )
        self.assertEqual(
            offenders,
            [],
            "DNS-query-issuing modules imported in SSRF-sensitive code:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_explicit_txt_or_mx_lookups(self):
        offenders: list[str] = []
        # Match string literals 'TXT' / 'MX' / 'NS' as the SECOND argument
        # to a resolve()-like call. We use a loose regex; false positives
        # in comments / log messages are filtered by skipping `#` lines.
        pattern = re.compile(
            r"""\.resolve\(\s*[^,]+,\s*['"](TXT|MX|NS|CNAME|SRV)['"]""",
        )
        for root in SRC_ROOTS:
            for path in root.rglob("*.py"):
                if "test" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for line_num, line in enumerate(text.splitlines(), start=1):
                    if line.lstrip().startswith("#"):
                        continue
                    if pattern.search(line):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{line_num} {line.strip()}"
                        )
        self.assertEqual(
            offenders,
            [],
            "Explicit DNS TXT/MX/NS/CNAME/SRV lookups found:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
