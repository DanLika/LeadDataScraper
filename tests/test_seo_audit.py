import pytest
from unittest.mock import MagicMock
import sys

@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock dependencies strictly for the scope of tests in this file without poisoning the global environment."""

    # Store original sys.modules
    original_modules = sys.modules.copy()

    # Setup mocks
    mock_aiohttp = MagicMock()
    mock_aiohttp.resolver = MagicMock()
    mock_aiohttp.resolver.DefaultResolver = MagicMock

    # Inject mocks
    sys.modules['bs4'] = MagicMock()
    sys.modules['aiohttp'] = mock_aiohttp
    sys.modules['aiohttp.resolver'] = mock_aiohttp.resolver

    # Yield control to the tests
    yield

    # Restore original sys.modules completely
    sys.modules.clear()
    sys.modules.update(original_modules)

    # Clean up imported target modules if they exist so they don't persist with mocked dependencies
    if 'src.scrapers.seo_audit' in sys.modules:
        del sys.modules['src.scrapers.seo_audit']
    if 'src.utils.ssrf_guard' in sys.modules:
        del sys.modules['src.utils.ssrf_guard']


def test_calculate_seo_score_empty(mock_dependencies):
    # Import the target AFTER the mocks are setup
    from src.scrapers.seo_audit import calculate_seo_score
    # Empty dict: score = 0 + 10 (response_time < 2.0 defaults to 0) + 10 (len(red_flags) == 0 defaults to []) = 20
    assert calculate_seo_score({}) == 20

def test_calculate_seo_score_perfect(mock_dependencies):
    from src.scrapers.seo_audit import calculate_seo_score
    perfect_results = {
        "has_ssl": True, # 10
        "title": "A Perfect Title", # 10
        "meta_description": "A perfect description.", # 10
        "h1_count": 1, # 10
        "tech_flags": {
            "has_viewport": True, # 10
            "has_google_analytics": True, # 10
            "has_facebook_pixel": True, # 10
            "has_robots_txt": True,
            "has_sitemap": True # robots + sitemap = 10
        },
        "response_time": 1.0, # 10
        "red_flags": [] # 10
    }
    # Total would be 100
    assert calculate_seo_score(perfect_results) == 100

def test_calculate_seo_score_partial(mock_dependencies):
    from src.scrapers.seo_audit import calculate_seo_score
    partial_results = {
        "has_ssl": True, # 10
        "title": "", # 0
        "meta_description": "Desc", # 10
        "h1_count": 2, # 0
        "tech_flags": {
            "has_viewport": False, # 0
            "has_gtm": True, # 10 (alternative to GA)
            "has_facebook_pixel": False, # 0
            "has_robots_txt": True,
            "has_sitemap": False # robots + sitemap = 0
        },
        "response_time": 3.0, # 0
        "red_flags": ["Missing H1"] # 0
    }
    # Total: 10 + 10 + 10 = 30
    assert calculate_seo_score(partial_results) == 30

def test_calculate_seo_score_max_cap(mock_dependencies):
    from src.scrapers.seo_audit import calculate_seo_score
    excess_results = {
        "has_ssl": True, # 10
        "title": "A Perfect Title", # 10
        "meta_description": "A perfect description.", # 10
        "h1_count": 1, # 10
        "tech_flags": {
            "has_viewport": True, # 10
            "has_google_analytics": True, # 10
            "has_gtm": True, # No extra points
            "has_facebook_pixel": True, # 10
            "has_robots_txt": True,
            "has_sitemap": True # 10
        },
        "response_time": 1.0, # 10
        "red_flags": [] # 10
    }
    # Technically 100 points, capped at 100
    assert calculate_seo_score(excess_results) == 100
