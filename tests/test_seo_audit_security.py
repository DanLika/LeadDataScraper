import unittest
import asyncio
import os
import aiohttp
from unittest.mock import patch, MagicMock

from src.scrapers.seo_audit import perform_seo_audit_async

class TestSeoAuditSecurity(unittest.IsolatedAsyncioTestCase):

    async def test_ssl_fallback_disabled_by_default(self):
        """Test that the insecure SSL fallback is disabled by default."""
        url = "https://example-bad-ssl.com"

        # We patch 'aiohttp.ClientSession.get' directly
        # The first call (normal get) will raise ClientConnectorSSLError
        # If fallback is enabled, it would call it again. If disabled, it should catch the error
        # and then results["is_up"] should be False.

        mock_get = MagicMock()
        mock_get.side_effect = aiohttp.ClientConnectorSSLError(None, os.error())

        # Ensure the environment variable is not set or is false
        with patch.dict(os.environ, {"ALLOW_INSECURE_SSL": "false"}):
            with patch("aiohttp.ClientSession.get", mock_get):
                results = await perform_seo_audit_async(url)

                # Verify that it caught the SSL error and added the correct flags
                self.assertFalse(results["is_up"])
                self.assertFalse(results["has_ssl"])
                self.assertIn("SSL Certificate Error", results["red_flags"])
                self.assertIn("Insecure SSL fallback disabled", results["red_flags"])

                # Check that it only attempted the connection once (no fallback)
                self.assertEqual(mock_get.call_count, 1)


    async def test_ssl_fallback_enabled(self):
        """Test that the insecure SSL fallback is enabled when ALLOW_INSECURE_SSL is true."""
        url = "https://example-bad-ssl.com"

        # We need to simulate the first call failing, and the second call succeeding
        # MagicMock side_effect with an iterable returns the elements in order

        class MockResponse:
            def __init__(self, text):
                self._text = text
                self.status = 200
            async def text(self):
                return self._text
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_get = MagicMock()
        mock_get.side_effect = [
            aiohttp.ClientConnectorSSLError(None, os.error()), # First call fails with SSL error
            MockResponse("<html><body><title>Test Page</title></body></html>") # Second call (fallback) succeeds
        ]

        with patch.dict(os.environ, {"ALLOW_INSECURE_SSL": "true"}):
            with patch("aiohttp.ClientSession.get", mock_get):
                results = await perform_seo_audit_async(url)

                # Verify that it caught the SSL error but successfully fell back
                self.assertTrue(results["is_up"])
                self.assertFalse(results["has_ssl"])
                self.assertIn("SSL Certificate Error", results["red_flags"])
                self.assertNotIn("Insecure SSL fallback disabled", results["red_flags"])
                self.assertEqual(results["title"], "Test Page")

                # Check that it attempted the connection twice (initial + fallback)
                self.assertEqual(mock_get.call_count, 2)

                # Verify the second call was made with fallback_ssl context
                _, kwargs = mock_get.call_args_list[1]
                self.assertIn('ssl', kwargs)
                self.assertFalse(kwargs['ssl'].check_hostname)

if __name__ == "__main__":
    unittest.main()
