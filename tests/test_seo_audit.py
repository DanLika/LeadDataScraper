import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import ssl

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.scrapers.seo_audit import perform_seo_audit_async

class MockGetContext1:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        # We need to raise the real SSLError or a generic Exception if aiohttp is fully mocked
        # Because sys.modules['aiohttp'] might be a MagicMock, we just raise an Exception that looks like it
        # Actually, let's just import aiohttp inside the test function if we need it, or we can use the original exception.
        import aiohttp
        os_error = OSError(1, "SSL Error")
        # Ensure we're using the real exception if possible, or fallback
        if hasattr(aiohttp, 'ClientConnectorSSLError') and not isinstance(aiohttp.ClientConnectorSSLError, MagicMock):
            raise aiohttp.ClientConnectorSSLError(connection_key=MagicMock(), os_error=os_error)
        else:
            # If globally mocked by other tests, raise a built-in SSLError
            raise ssl.SSLError("SSL Error")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class MockGetContext2:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        class DummyResponse:
            async def text(self):
                return "<html><head><title>Test Title That Is The Right Length</title></head><body><h1>Fallback Success</h1></body></html>"
        return DummyResponse()
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class DummySession:
    def __init__(self):
        self.call_count = 0
        self.call_args_list = []

    def get(self, *args, **kwargs):
        self.call_count += 1
        self.call_args_list.append((args, kwargs))
        if 'ssl' in kwargs:
            return MockGetContext2()
        return MockGetContext1()

@pytest.mark.asyncio
async def test_perform_seo_audit_async_ssl_error():
    dummy_session = DummySession()

    class MockSessionContext:
        async def __aenter__(self):
            return dummy_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch('src.scrapers.seo_audit.aiohttp.ClientSession', return_value=MockSessionContext()):
        results = await perform_seo_audit_async("https://bad-ssl.com")

        # Verify fallback logic and red_flags
        assert "SSL Certificate Error" in results["red_flags"]
        assert results["has_ssl"] is False
        assert results["is_up"] is True

        # We can also verify that get was called twice
        assert dummy_session.call_count == 2
        # The second call should have ssl=fallback_ssl
        kwargs = dummy_session.call_args_list[1][1]
        assert 'ssl' in kwargs
        assert kwargs['ssl'].check_hostname is False
        assert kwargs['ssl'].verify_mode == ssl.CERT_NONE
