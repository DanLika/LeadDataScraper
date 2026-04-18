import pytest
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scrapers.seo_audit import calculate_seo_score

def test_calculate_seo_score_max():
    """Test a perfect score."""
    results = {
        "has_ssl": True,
        "title": "A good title",
        "meta_description": "A good meta description",
        "h1_count": 1,
        "tech_flags": {
            "has_viewport": True,
            "has_google_analytics": True,
            "has_gtm": False,
            "has_facebook_pixel": True,
            "has_robots_txt": True,
            "has_sitemap": True
        },
        "response_time": 1.5,
        "red_flags": []
    }
    assert calculate_seo_score(results) == 100

def test_calculate_seo_score_min():
    """Test a true minimum score (0)."""
    # Needs a slow response time and red flags to get 0 points for Advanced & Health
    results = {
        "response_time": 5.0, # >= 2.0 -> 0 points
        "red_flags": ["Missing SSL", "Slow site"] # len > 0 -> 0 points
    }
    assert calculate_seo_score(results) == 0

def test_calculate_seo_score_empty():
    """Test an empty score dict."""
    # An empty dict evaluates response_time to 0 (<2.0) and red_flags to [] (len==0)
    # So it naturally gets 20 points.
    assert calculate_seo_score({}) == 20

def test_calculate_seo_score_partial_core():
    """Test partial core SEO points."""
    results = {
        "has_ssl": True,
        "title": "Title only",
        # missing meta_description
        "h1_count": 2 # > 1, so no points
    }
    # 10 for ssl, 10 for title = 20
    # +20 for default response_time & red_flags
    assert calculate_seo_score(results) == 40

def test_calculate_seo_score_tech_gtm_fallback():
    """Test GTM fallback points."""
    results = {
        "tech_flags": {
            "has_gtm": True,
            "has_google_analytics": False
        }
    }
    # 10 for GTM
    # +20 for default response_time & red_flags
    assert calculate_seo_score(results) == 30

def test_calculate_seo_score_advanced_and_health():
    """Test advanced health flags."""
    # Bad health
    results = {
        "response_time": 2.5, # No points (>= 2.0)
        "tech_flags": {
            "has_robots_txt": True,
            "has_sitemap": False # Needs both
        },
        "red_flags": ["Some issue"] # len > 0
    }
    assert calculate_seo_score(results) == 0

    # Good health
    results_good = {
        "response_time": 1.9, # 10
        "tech_flags": {
            "has_robots_txt": True,
            "has_sitemap": True # 10
        },
        "red_flags": [] # 10
    }
    assert calculate_seo_score(results_good) == 30

def test_calculate_seo_score_cap():
    """Ensure score does not exceed 100."""
    # This shouldn't happen with the exact point values unless we manually test the min() logic
    # We can mock or just verify the normal max works, which is exactly 100.
    results = {
        "has_ssl": True,
        "title": "A good title",
        "meta_description": "A good meta description",
        "h1_count": 1,
        "tech_flags": {
            "has_viewport": True,
            "has_google_analytics": True,
            "has_gtm": True, # extra points? no, 'or' condition
            "has_facebook_pixel": True,
            "has_robots_txt": True,
            "has_sitemap": True
        },
        "response_time": 1.5,
        "red_flags": []
    }
    assert calculate_seo_score(results) == 100
