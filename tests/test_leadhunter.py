import pytest
from unittest.mock import MagicMock
import sys

# In highly restricted sandbox environments where pip install is blocked and dependencies are missing,
# use conditional sys.modules injection (e.g., if 'bs4' not in sys.modules: sys.modules['bs4'] = MagicMock())
# at the top of test files before importing source modules to allow pytest to collect and run tests
# despite missing packages.

if 'aiohttp' not in sys.modules:
    mock_aiohttp = MagicMock()
    mock_aiohttp.ClientSession = MagicMock()
    mock_aiohttp.ClientTimeout = MagicMock()
    mock_aiohttp.TCPConnector = MagicMock()
    sys.modules['aiohttp'] = mock_aiohttp

if 'aiohttp.resolver' not in sys.modules:
    sys.modules['aiohttp.resolver'] = MagicMock()

if 'google' not in sys.modules:
    sys.modules['google'] = MagicMock()
if 'google.genai' not in sys.modules:
    sys.modules['google.genai'] = MagicMock()
if 'google.genai.types' not in sys.modules:
    sys.modules['google.genai.types'] = MagicMock()
if 'bs4' not in sys.modules:
    sys.modules['bs4'] = MagicMock()
if 'fastapi' not in sys.modules:
    sys.modules['fastapi'] = MagicMock()

from src.processors.leadhunter import LeadHunter

class TestLeadHunter:
    @pytest.fixture
    def hunter(self):
        return LeadHunter()

    def test_extract_personal_name_empty_or_unknown(self, hunter):
        assert hunter.extract_personal_name("") is None
        assert hunter.extract_personal_name(None) is None
        assert hunter.extract_personal_name("Unknown") is None
        assert hunter.extract_personal_name("n/a") is None
        assert hunter.extract_personal_name("None") is None

    def test_extract_personal_name_valid(self, hunter):
        assert hunter.extract_personal_name("john doe") == "John"
        assert hunter.extract_personal_name("jane") == "Jane"

    def test_extract_personal_name_with_titles(self, hunter):
        assert hunter.extract_personal_name("Dr. Smith") == "Smith"
        assert hunter.extract_personal_name("CEO Alice") == "Alice"
        assert hunter.extract_personal_name("Founder & Bob") == "Bob"

    def test_extract_personal_name_short_strings(self, hunter):
        assert hunter.extract_personal_name("A Lincoln") == "Lincoln"
        assert hunter.extract_personal_name("X Corp") == "Corp"

    def test_extract_personal_name_complex_separators(self, hunter):
        assert hunter.extract_personal_name("Mr/John/Doe") == "John"
        assert hunter.extract_personal_name("Mrs Jane (Director)") == "Jane"
