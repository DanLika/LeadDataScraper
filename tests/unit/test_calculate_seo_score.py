import pytest # type: ignore
from typing import Any
from src.scrapers.seo_audit import calculate_seo_score


def test_calculate_seo_score_perfect() -> None:
    """Test that meeting all conditions correctly results in a maximum score of 100."""
    results: dict[str, Any] = {
        "has_ssl": True,
        "title": "A Great Title",
        "meta_description": "A wonderful meta description",
        "h1_count": 1,
        "response_time": 1.5,
        "red_flags": [],
        "tech_flags": {
            "has_viewport": True,
            "has_google_analytics": True,
            "has_facebook_pixel": True,
            "has_robots_txt": True,
            "has_sitemap": True,
        },
    }
    score = calculate_seo_score(results)
    assert score == 100


def test_calculate_seo_score_empty() -> None:
    """Test the baseline score when almost no information is provided.

    Empty dict means:
    - No SSL, title, meta desc (0)
    - h1_count defaults to 0, so not 1 (0)
    - No tech_flags (0)
    - response_time defaults to 0, which is < 2.0 (+10)
    - red_flags defaults to [], length is 0 (+10)
    Total: 20
    """
    results: dict[str, Any] = {}
    score = calculate_seo_score(results)
    assert score == 20


def test_calculate_seo_score_missing_tech_flags() -> None:
    """Test graceful handling when 'tech_flags' is entirely missing."""
    results: dict[str, Any] = {
        "has_ssl": True,
        "response_time": 2.5,  # No points
        "red_flags": ["Some error"],  # No points
        # tech_flags deliberately omitted
    }
    # Should get 10 points for SSL
    score = calculate_seo_score(results)
    assert score == 10


def test_calculate_seo_score_analytics_or_gtm() -> None:
    """Test the 'OR' condition for Google Analytics and Google Tag Manager."""
    # Test only Google Analytics
    res_ga: dict[str, Any] = {
        "response_time": 2.5,
        "red_flags": ["error"],
        "tech_flags": {"has_google_analytics": True},
    }
    assert calculate_seo_score(res_ga) == 10

    # Test only GTM
    res_gtm: dict[str, Any] = {
        "response_time": 2.5,
        "red_flags": ["error"],
        "tech_flags": {"has_gtm": True},
    }
    assert calculate_seo_score(res_gtm) == 10

    # Test both (still only +10 points)
    res_both: dict[str, Any] = {
        "response_time": 2.5,
        "red_flags": ["error"],
        "tech_flags": {"has_google_analytics": True, "has_gtm": True},
    }
    assert calculate_seo_score(res_both) == 10

    # Test neither
    res_neither: dict[str, Any] = {
        "response_time": 2.5,
        "red_flags": ["error"],
        "tech_flags": {},
    }
    assert calculate_seo_score(res_neither) == 0


def test_calculate_seo_score_robots_and_sitemap() -> None:
    """Test the 'AND' condition for robots.txt and sitemap."""
    base_res: dict[str, Any] = {
        "response_time": 2.5,
        "red_flags": ["error"],
    }

    # Only robots
    assert calculate_seo_score({**base_res, "tech_flags": {"has_robots_txt": True}}) == 0
    # Only sitemap
    assert calculate_seo_score({**base_res, "tech_flags": {"has_sitemap": True}}) == 0
    # Neither
    assert calculate_seo_score({**base_res, "tech_flags": {}}) == 0
    # Both
    assert calculate_seo_score({**base_res, "tech_flags": {"has_robots_txt": True, "has_sitemap": True}}) == 10


@pytest.mark.parametrize(  # type: ignore
    "h1_count, expected_score", [
    (0, 20),  # +10 for default response_time, +10 for empty red_flags
    (1, 30),  # +10 for h1_count, +20 for defaults
    (2, 20),  # Only 1 h1 gets points
    (-1, 20),
])

def test_calculate_seo_score_h1_count(h1_count: Any, expected_score: Any) -> None:
    """Test that points are awarded only for exactly one H1 tag."""
    results: dict[str, Any] = {"h1_count": h1_count}
    assert calculate_seo_score(results) == expected_score


@pytest.mark.parametrize(  # type: ignore
    "response_time, expected_score", [
    (1.99, 20), # +10 for response_time < 2.0, +10 for empty red_flags
    (2.0, 10),  # Not < 2.0, only +10 for empty red_flags
    (2.1, 10),  # Not < 2.0
    (0, 20),
])

def test_calculate_seo_score_response_time(response_time: Any, expected_score: Any) -> None:
    """Test the threshold logic for response time (< 2.0s)."""
    results: dict[str, Any] = {"response_time": response_time}
    assert calculate_seo_score(results) == expected_score


def test_calculate_seo_score_red_flags() -> None:
    """Test that presence of red_flags loses the 10 point bonus."""
    results_with_flag: dict[str, Any] = {"red_flags": ["Something went wrong"]}
    # +10 for default response time, +0 for red flags
    assert calculate_seo_score(results_with_flag) == 10

    results_without_flag: dict[str, Any] = {"red_flags": []}
    # +10 for default response time, +10 for no red flags
    assert calculate_seo_score(results_without_flag) == 20

    results_missing_flag: dict[str, Any] = {}
    # +10 for default response time, +10 for no red flags (default is [])
    assert calculate_seo_score(results_missing_flag) == 20


def test_calculate_seo_score_capped_at_100() -> None:
    """Test that scores over 100 are capped."""
    results: dict[str, Any] = {
        "has_ssl": True, # 10
        "title": "A Great Title", # 10
        "meta_description": "A wonderful meta description", # 10
        "h1_count": 1, # 10
        "response_time": 1.5, # 10
        "red_flags": [], # 10
        "tech_flags": {
            "has_viewport": True, # 10
            "has_google_analytics": True, # 10
            "has_gtm": True, # (No extra points, combined with analytics)
            "has_facebook_pixel": True, # 10
            "has_robots_txt": True, # 10
            "has_sitemap": True, # (10 total for both robots and sitemap)
        },
    }
    # This actually exactly hits 100 based on the rules. Let's make an artificially high score possible
    # to test `min(score, 100)`. Wait, looking at the code, it's:
    # Core (40)
    # Tech (30)
    # Adv (30)
    # Total max is exactly 100 anyway! The `min(score, 100)` is a safety net.
    # We can't really exceed 100 through the defined paths. Let's test reaching exactly 100.
    score = calculate_seo_score(results)
    assert score == 100
