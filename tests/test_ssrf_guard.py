import sys
from unittest.mock import MagicMock

if 'aiohttp' not in sys.modules:
    sys.modules['aiohttp'] = MagicMock()
if 'aiohttp.resolver' not in sys.modules:
    sys.modules['aiohttp.resolver'] = MagicMock()

import unittest
from src.utils.ssrf_guard import assert_safe_scheme, SSRFError

class TestAssertSafeScheme(unittest.TestCase):
    def test_valid_http_https(self):
        try:
            assert_safe_scheme("http://example.com")
            assert_safe_scheme("https://example.com/path")
        except SSRFError:
            self.fail("assert_safe_scheme() raised SSRFError unexpectedly!")

    def test_invalid_schemes(self):
        with self.assertRaisesRegex(SSRFError, "Blocked URL scheme: 'file'"):
            assert_safe_scheme("file:///etc/passwd")
        with self.assertRaisesRegex(SSRFError, "Blocked URL scheme: 'ftp'"):
            assert_safe_scheme("ftp://example.com")
        with self.assertRaisesRegex(SSRFError, "Blocked URL scheme: ''"):
            assert_safe_scheme("example.com")

    def test_no_host(self):
        with self.assertRaisesRegex(SSRFError, "URL has no host"):
            assert_safe_scheme("http://")

    def test_blocked_hostnames(self):
        with self.assertRaisesRegex(SSRFError, "Blocked hostname: metadata.google.internal"):
            assert_safe_scheme("http://metadata.google.internal")

    def test_suspicious_numeric_hosts(self):
        with self.assertRaisesRegex(SSRFError, r"Suspicious numeric host '0177\.0\.0\.1' \(non-standard IP literal — octal/leading-zero\)"):
            assert_safe_scheme("http://0177.0.0.1")

    def test_blocked_non_public_ips(self):
        with self.assertRaisesRegex(SSRFError, "Blocked non-public IP 127.0.0.1 for host '127.0.0.1'"):
            assert_safe_scheme("http://127.0.0.1")
        with self.assertRaisesRegex(SSRFError, "Blocked non-public IP 169.254.169.254 for host '169.254.169.254'"):
            assert_safe_scheme("http://169.254.169.254")
        with self.assertRaisesRegex(SSRFError, "Blocked non-public IP 10.0.0.1 for host '10.0.0.1'"):
            assert_safe_scheme("http://10.0.0.1")

if __name__ == '__main__':
    unittest.main()
