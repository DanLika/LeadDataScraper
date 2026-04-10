import sys
import os
import unittest
from unittest.mock import patch
import aiohttp
import ssl

from src.scrapers.seo_audit import perform_seo_audit_async

class MockResponse:
    def __init__(self, text_data="<html><body><h1>Test</h1></body></html>", status=200):
        self.text_data = text_data
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def text(self):
        return self.text_data

class MockSession:
    def __init__(self, *args, **kwargs):
        self.get_calls = []
        self.raise_on_get = kwargs.pop('raise_on_get', None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def get(self, url, *args, **kwargs):
        self.get_calls.append((url, kwargs))
        if self.raise_on_get:
            if isinstance(self.raise_on_get, list):
                if not self.raise_on_get:
                    return MockResponse()
                exc = self.raise_on_get.pop(0)
                if exc:
                    raise exc
            else:
                raise self.raise_on_get

        return MockResponse()

class TestSeoAuditEdgeCases(unittest.IsolatedAsyncioTestCase):

    async def test_invalid_url(self):
        # URL is None
        res = await perform_seo_audit_async(None)
        self.assertEqual(res['url'], None)
        self.assertEqual(res['is_up'], False)
        self.assertEqual(res['score'], 0)

        # URL is empty
        res = await perform_seo_audit_async("")
        self.assertEqual(res['url'], "")
        self.assertEqual(res['is_up'], False)
        self.assertEqual(res['score'], 0)

        # URL is wrong type
        res = await perform_seo_audit_async(123)
        self.assertEqual(res['url'], 123)
        self.assertEqual(res['is_up'], False)
        self.assertEqual(res['score'], 0)

    @patch('src.scrapers.seo_audit.aiohttp.ClientSession')
    async def test_protocol_added(self, mock_session_class):
        mock_session = MockSession()
        mock_session_class.return_value = mock_session

        res = await perform_seo_audit_async("google.com")

        self.assertTrue(res['is_up'])
        self.assertEqual(res['url'], 'https://google.com')
        self.assertTrue(res['has_ssl'])

        self.assertEqual(len(mock_session.get_calls), 1)
        self.assertEqual(mock_session.get_calls[0][0], 'https://google.com')

    @patch('src.scrapers.seo_audit.aiohttp.ClientSession')
    async def test_generic_exception(self, mock_session_class):
        mock_session = MockSession(raise_on_get=Exception("Connection refused"))
        mock_session_class.return_value = mock_session

        res = await perform_seo_audit_async("https://error.com")

        self.assertFalse(res['is_up'])
        self.assertTrue(any("Connection Failed: Connection refused" in flag for flag in res['red_flags']))

    @patch('src.scrapers.seo_audit.aiohttp.ClientSession')
    async def test_ssl_error_fallback(self, mock_session_class):
        mock_session = MockSession(raise_on_get=[ssl.SSLError("SSL cert verification failed"), None])
        mock_session_class.return_value = mock_session

        res = await perform_seo_audit_async("https://badssl.com")

        self.assertTrue(res['is_up'])
        self.assertFalse(res['has_ssl'])
        self.assertIn("SSL Certificate Error", res['red_flags'])
        self.assertEqual(len(mock_session.get_calls), 2)

        # verify the second get call has the fallback ssl context
        second_call_kwargs = mock_session.get_calls[1][1]
        self.assertIn('ssl', second_call_kwargs)
        fallback_ssl = second_call_kwargs['ssl']
        self.assertEqual(fallback_ssl.check_hostname, False)
        self.assertEqual(fallback_ssl.verify_mode, ssl.CERT_NONE)

if __name__ == '__main__':
    unittest.main()
