import pytest
import sys
import asyncio
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock missing modules before any application imports"""
    import sys
    from unittest.mock import MagicMock

    # Save original modules
    original_modules = sys.modules.copy()

    # Create module mocks
    class RecursiveMock(MagicMock):
        def __getattr__(self, name):
            if name in ['__mro__', '__bases__', '_mock_methods', '_mock_unsafe', '__class__']:
                return super().__getattr__(name)
            if name == '__mro_entries__':
                def _mro_entries(bases):
                    return (RecursiveMock,)
                return _mro_entries
            return RecursiveMock()

    sys.modules['playwright'] = RecursiveMock()
    sys.modules['playwright.async_api'] = RecursiveMock()
    sys.modules['google'] = RecursiveMock()
    sys.modules['google.genai'] = RecursiveMock()
    sys.modules['google.genai.types'] = RecursiveMock()
    sys.modules['dotenv'] = RecursiveMock()
    sys.modules['supabase'] = RecursiveMock()
    sys.modules['httpx'] = RecursiveMock()
    sys.modules['aiohttp'] = RecursiveMock()
    sys.modules['aiohttp.resolver'] = RecursiveMock()
    sys.modules['anthropic'] = RecursiveMock()
    sys.modules['pandas'] = RecursiveMock()
    sys.modules['numpy'] = RecursiveMock()
    sys.modules['postgrest'] = RecursiveMock()
    sys.modules['gotrue'] = RecursiveMock()
    sys.modules['bs4'] = RecursiveMock()
    sys.modules['fastapi'] = RecursiveMock()
    sys.modules['pydantic'] = RecursiveMock()
    sys.modules['redis'] = RecursiveMock()
    sys.modules['pytest_asyncio'] = RecursiveMock()

    yield

    # Restore original modules
    sys.modules.clear()
    sys.modules.update(original_modules)

@pytest.fixture
def discovery_engine():
    from src.scrapers.discovery_engine import DiscoveryEngine
    engine = DiscoveryEngine()
    engine.db = MagicMock()
    return engine

def test_enrich_and_save_empty_leads(discovery_engine):
    # Setup
    leads = []

    # Act
    asyncio.run(discovery_engine.enrich_and_save(leads))

    # Assert
    discovery_engine.db.upsert_leads.assert_not_called()

def test_enrich_and_save_with_leads(discovery_engine):
    # Setup
    leads = [
        {"name": "Lead 1", "website": "example.com"},
        {"name": "Lead 2", "website": "example.org"}
    ]

    # Act
    asyncio.run(discovery_engine.enrich_and_save(leads))

    # Assert
    discovery_engine.db.upsert_leads.assert_called_once_with(leads)

def test_enrich_and_save_none(discovery_engine):
    # Setup
    leads = None

    # Act
    asyncio.run(discovery_engine.enrich_and_save(leads))

    # Assert
    discovery_engine.db.upsert_leads.assert_not_called()
