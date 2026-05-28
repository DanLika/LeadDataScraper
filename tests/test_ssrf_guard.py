"""Direct unit tests for the SSRF guard core logic in
`src/utils/ssrf_guard.py`.

`test_security_defenses.py` verifies the guard is *wired* into the
enrichment engine + Playwright route handler, but mocks
`assert_safe_url` — so the actual blocking logic (IP classification,
hostname denylist, scheme allowlist, numeric-host octal trick) had no
direct coverage. The crawler pentest (PENTEST_CRAWLER.md Round 1)
exercised it live but ephemerally; this file locks it in CI.

Network: the only DNS-resolving case uses `localhost`, which resolves
locally without a network round-trip. Every other case uses IP
literals or the hostname denylist — no DNS.
"""

import ipaddress
import socket
import unittest

from src.utils.ssrf_guard import (
    SSRFError,
    SSRFGuardResolver,
    assert_safe_scheme,
    assert_safe_url,
    _assert_public_ip,
)


class TestAssertSafeScheme(unittest.TestCase):
    def test_https_and_http_allowed(self):
        assert_safe_scheme("https://example.com/")  # no raise
        assert_safe_scheme("http://example.com/")

    def test_dangerous_schemes_rejected(self):
        for url in [
            "file:///etc/passwd",
            "ftp://example.com/",
            "gopher://example.com/",
            "javascript:alert(1)",
            "data:text/html,<script>x</script>",
        ]:
            with self.assertRaises(SSRFError, msg=url):
                assert_safe_scheme(url)

    def test_url_with_no_host_rejected(self):
        with self.assertRaises(SSRFError):
            assert_safe_scheme("https://")

    def test_cloud_metadata_hostnames_rejected(self):
        for host in [
            "metadata.google.internal",
            "metadata.goog",
            "metadata",
            "instance-data",
            "instance-data.ec2.internal",
            "metadata.azure.com",
            "metadata.oraclecloud.com",
            "metadata.alibabacloud.com",
            "metadata.tencentcloudapi.com",
            "kubernetes.default.svc",
            "kubernetes.default.svc.cluster.local",
        ]:
            with self.assertRaises(SSRFError, msg=host):
                assert_safe_scheme(f"http://{host}/latest/meta-data/")

    def test_metadata_host_rejected_with_trailing_dot(self):
        # `host.rstrip(".")` must catch the FQDN-with-trailing-dot form.
        with self.assertRaises(SSRFError):
            assert_safe_scheme("http://metadata.google.internal./")

    def test_private_ip_literals_rejected(self):
        for host in [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.1",
            "169.254.169.254",
            "0.0.0.0",
            "[::1]",
        ]:
            with self.assertRaises(SSRFError, msg=host):
                assert_safe_scheme(f"http://{host}/")

    def test_numeric_host_octal_decimal_trick_rejected(self):
        # Non-standard IP literals (leading-zero / plain-decimal) that
        # `ipaddress` won't parse but look numeric — classic SSRF bypass.
        for host in ["0177.0.0.1", "2130706433", "017700000001"]:
            with self.assertRaises(SSRFError, msg=host):
                assert_safe_scheme(f"http://{host}/")

    def test_public_hostname_passes(self):
        assert_safe_scheme("https://example.com/")  # no raise
        assert_safe_scheme("https://api.github.com/repos")

    def test_public_ip_literal_passes(self):
        assert_safe_scheme("https://8.8.8.8/")  # Google DNS — global


class TestAssertPublicIp(unittest.TestCase):
    def test_private_loopback_linklocal_rejected(self):
        for ip in [
            "127.0.0.1",
            "10.0.0.1",
            "192.168.0.1",
            "172.16.0.1",
            "169.254.0.1",
            "::1",
            "fc00::1",
        ]:
            with self.assertRaises(SSRFError, msg=ip):
                _assert_public_ip(ipaddress.ip_address(ip), ip)

    def test_multicast_reserved_unspecified_rejected(self):
        for ip in ["224.0.0.1", "240.0.0.1", "0.0.0.0"]:
            with self.assertRaises(SSRFError, msg=ip):
                _assert_public_ip(ipaddress.ip_address(ip), ip)

    def test_global_ip_passes(self):
        for ip in ["8.8.8.8", "1.1.1.1", "93.184.216.34"]:
            _assert_public_ip(ipaddress.ip_address(ip), ip)  # no raise


class TestAssertSafeUrl(unittest.IsolatedAsyncioTestCase):
    async def test_private_ip_literal_rejected_without_dns(self):
        for host in ["127.0.0.1", "10.0.0.1", "169.254.169.254"]:
            with self.assertRaises(SSRFError, msg=host):
                await assert_safe_url(f"http://{host}/")

    async def test_blocked_metadata_hostname_rejected(self):
        with self.assertRaises(SSRFError):
            await assert_safe_url("http://metadata.google.internal/")

    async def test_dangerous_scheme_rejected(self):
        with self.assertRaises(SSRFError):
            await assert_safe_url("file:///etc/passwd")

    async def test_localhost_resolves_to_loopback_and_is_rejected(self):
        # `localhost` DNS-resolves locally to 127.0.0.1 / ::1 — the
        # resolved-IP branch must reject it.
        with self.assertRaises(SSRFError):
            await assert_safe_url("http://localhost/")

    async def test_public_ip_literal_passes(self):
        await assert_safe_url("https://8.8.8.8/")  # no raise — global IP

    async def test_dns_resolution_failure_raises_ssrf_error(self):
        # A `.invalid` TLD (RFC 2606) is guaranteed NXDOMAIN — the
        # `socket.gaierror` branch must surface as an SSRFError, not leak
        # the raw resolver exception.
        with self.assertRaises(SSRFError):
            await assert_safe_url("https://nonexistent-zzz-9988random.invalid/")


class TestSSRFGuardResolver(unittest.IsolatedAsyncioTestCase):
    """The aiohttp resolver subclass used by the SEO-audit connector —
    it must reject blocked hostnames and non-public DNS results before
    aiohttp ever opens a socket."""

    async def test_blocked_metadata_hostname_rejected(self):
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("metadata.google.internal", 80, socket.AF_INET)

    async def test_blocked_hostname_case_insensitive(self):
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("KUBERNETES.DEFAULT.SVC", 80, socket.AF_INET)

    async def test_localhost_resolves_to_loopback_and_is_rejected(self):
        # `localhost` clears the hostname denylist but `super().resolve()`
        # returns 127.0.0.1 — the resolved-IP check must reject it.
        resolver = SSRFGuardResolver()
        with self.assertRaises(SSRFError):
            await resolver.resolve("localhost", 80, socket.AF_INET)

    async def test_public_host_resolves_and_returns_results(self):
        # A public host clears both checks and the resolver returns the
        # aiohttp result list unchanged.
        resolver = SSRFGuardResolver()
        results = await resolver.resolve("example.com", 80, socket.AF_INET)
        assert isinstance(results, list) and len(results) >= 1
        assert all("host" in r for r in results)


if __name__ == "__main__":
    unittest.main()
