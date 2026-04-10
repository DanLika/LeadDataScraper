import pytest
from src.scrapers.seo_audit import calculate_seo_score

def test_calculate_seo_score_base():
    """Test base score calculation."""
    base_score = calculate_seo_score({})
    assert isinstance(base_score, int)
    assert base_score >= 0

def test_calculate_seo_score_has_ssl():
    """Test that has_ssl adds 10 to the base score."""
    base_score = calculate_seo_score({})
    assert calculate_seo_score({'has_ssl': True}) == base_score + 10

def test_calculate_seo_score_title():
    """Test that title adds 10 to the base score."""
    base_score = calculate_seo_score({})
    assert calculate_seo_score({'title': 'Example Title'}) == base_score + 10

def test_calculate_seo_score_combined():
    """Test that both has_ssl and title add 20 to the base score."""
    base_score = calculate_seo_score({})
    assert calculate_seo_score({'has_ssl': True, 'title': 'Example Title'}) == base_score + 20
