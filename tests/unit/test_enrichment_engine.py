import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.scrapers.enrichment_engine import EnrichmentEngine, SSRFError

@pytest.fixture
def enrichment_engine():
    with patch("os.getenv", return_value=None):
        engine = EnrichmentEngine()
    yield engine

@pytest.mark.asyncio
async def test_enrich_lead_no_urls(enrichment_engine):
    lead = {"name": "Test Lead"}
    result = await enrichment_engine.enrich_lead(lead)

    assert result == {"name": "Test Lead"}
    assert "enrichment_status" not in result

@pytest.mark.asyncio
async def test_enrich_lead_browser_failure(enrichment_engine):
    lead = {"name": "Test Lead", "website": "http://example.com"}

    enrichment_engine._get_browser = AsyncMock(side_effect=Exception("Browser launch failed"))

    result = await enrichment_engine.enrich_lead(lead)

    assert result["enrichment_status"] == "FAILED_NO_CONTENT"
    assert "website" in result

@pytest.mark.asyncio
async def test_enrich_lead_successful(enrichment_engine):
    lead = {
        "name": "Test Lead",
        "website": "http://example.com",
        "about_url": "http://example.com/about",
        "team_url": "http://example.com",  # duplicate website
        "clients_url": "http://example.com/clients"
    }

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()

    # Valid content length > 100
    mock_page.evaluate = AsyncMock(return_value="A" * 150)

    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    enrichment_engine._get_browser = AsyncMock(return_value=mock_browser)

    # Mock SSRF guard to do nothing
    with patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock):
        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock):
            enrichment_engine.deep_ai_parse = AsyncMock(return_value={
                "company_name": "Test Company",
                "company_size": "Unknown",
                "leadership_team": "N/A",
                "key_offerings": "Plumbing",
                "contact_details": "null",
                "business_details": None,
                "target_clients": "Homeowners",
            })

            result = await enrichment_engine.enrich_lead(lead)

            assert result["enrichment_status"] == "COMPLETED"
            assert result["company_name"] == "Test Company"
            assert result["key_offerings"] == "Plumbing"
            assert result["target_clients"] == "Homeowners"

            # Check cleanup of Unknown, N/A, null, None
            assert "company_size" not in result
            assert "leadership_team" not in result
            assert "contact_details" not in result
            assert "business_details" not in result

@pytest.mark.asyncio
async def test_enrich_lead_fetch_page_ssrf_error(enrichment_engine):
    lead = {"name": "Test Lead", "website": "http://internal.app"}

    mock_browser = AsyncMock()
    mock_context = AsyncMock()

    mock_browser.new_context = AsyncMock(return_value=mock_context)
    enrichment_engine._get_browser = AsyncMock(return_value=mock_browser)

    with patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock):
        with patch("src.scrapers.enrichment_engine.assert_safe_url", side_effect=SSRFError("Blocked")):
            result = await enrichment_engine.enrich_lead(lead)

            assert result["enrichment_status"] == "FAILED_NO_CONTENT"

@pytest.mark.asyncio
async def test_enrich_lead_short_content(enrichment_engine):
    lead = {"name": "Test Lead", "website": "http://example.com"}

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()

    # Return content shorter than 100 characters
    mock_page.evaluate = AsyncMock(return_value="Short")

    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    enrichment_engine._get_browser = AsyncMock(return_value=mock_browser)

    with patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock):
        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock):
            result = await enrichment_engine.enrich_lead(lead)

            assert result["enrichment_status"] == "FAILED_NO_CONTENT"

@pytest.mark.asyncio
async def test_enrich_lead_fetch_page_exception(enrichment_engine):
    lead = {"name": "Test Lead", "website": "http://example.com"}

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()

    # Raise exception during goto
    mock_page.goto = AsyncMock(side_effect=Exception("Navigation failed"))

    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    enrichment_engine._get_browser = AsyncMock(return_value=mock_browser)

    with patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock):
        with patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock):
            result = await enrichment_engine.enrich_lead(lead)

            assert result["enrichment_status"] == "FAILED_NO_CONTENT"
