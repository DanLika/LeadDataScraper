import sys
from unittest.mock import MagicMock

class MockResolver:
    pass

class DeepMock(MagicMock):
    def __getattr__(self, name):
        if name in ('__bases__', '__class__', '__mro__', '_mock_methods', '_mock_children', '_mock_name', '_mock_new_name', '_mock_new_parent'):
            return super().__getattr__(name)
        if name == 'DefaultResolver':
            return MockResolver
        return DeepMock()

class APIError(Exception):
    pass

for module in ['pandas', 'pandas.errors', 'google', 'google.genai', 'google.genai.types', 'fastapi', 'fastapi.testclient', 'fastapi.exceptions', 'fastapi.middleware', 'fastapi.middleware.cors', 'fastapi.responses', 'fastapi.security', 'pydantic', 'numpy', 'aiohttp', 'aiohttp.resolver', 'bs4', 'playwright', 'playwright.async_api', 'supabase', 'postgrest', 'postgrest.exceptions', 'dotenv', 'uvicorn', 'urllib3', 'aiofiles', 'aiofiles.os', 'slowapi', 'slowapi.errors', 'slowapi.util']:
    if module not in sys.modules:
        if module == 'aiohttp.resolver':
            m = DeepMock()
            m.DefaultResolver = MockResolver
            sys.modules[module] = m
        elif module == 'postgrest.exceptions':
            m = DeepMock()
            m.APIError = APIError
            sys.modules[module] = m
        else:
            sys.modules[module] = DeepMock()

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(["tests/"]))
