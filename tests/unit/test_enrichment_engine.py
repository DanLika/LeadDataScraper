import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from src.scrapers.enrichment_engine import EnrichmentEngine


@pytest.fixture
def enrichment_engine():
    # Provide dummy API key config so init succeeds
    with patch.dict("os.environ", {"GEMINI_API_KEY": "dummy_key"}):
        engine = EnrichmentEngine()
    yield engine


@pytest.mark.asyncio
async def test_enrich_lead_no_urls(enrichment_engine):
    lead = {"name": "Test Co", "email": "test@test.com"}
    result = await enrichment_engine.enrich_lead(lead.copy())

    assert result == lead
    assert "enrichment_status" not in result


@pytest.mark.asyncio
async def test_enrich_lead_success(enrichment_engine):
    lead = {"name": "Success Co", "website": "https://success.com"}

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()

    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    # String must be > 100 chars to pass the content length filter
    mock_page.evaluate.return_value = "This is a great website with lots of info. " * 5

    mock_ai_parse_return = {
        "company_name": "Success Co",
        "company_size": "Unknown", # should be filtered out
        "leadership_team": "John Doe",
        "key_offerings": "null", # should be filtered out
        "contact_details": "N/A", # should be filtered out
        "business_details": "They do things.",
        "target_clients": None, # should be filtered out
        "pain_points": "Many"
    }

    with patch.object(enrichment_engine, '_get_browser', return_value=mock_browser), \
         patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock), \
         patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock), \
         patch.object(enrichment_engine, 'deep_ai_parse', return_value=mock_ai_parse_return) as mock_parse:

        result = await enrichment_engine.enrich_lead(lead)

        assert result["enrichment_status"] == "COMPLETED"
        assert result["company_name"] == "Success Co"
        assert result["leadership_team"] == "John Doe"
        assert result["business_details"] == "They do things."
        assert result["pain_points"] == "Many"

        # Verify filtered out fields
        assert "company_size" not in result
        assert "key_offerings" not in result
        assert "contact_details" not in result
        assert "target_clients" not in result

        mock_parse.assert_called_once()
        assert len(mock_parse.call_args[0][0]) == 1 # 1 content block


@pytest.mark.asyncio
async def test_enrich_lead_no_content(enrichment_engine):
    lead = {"name": "Empty Co", "website": "https://empty.com"}

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()

    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    # Simulating no valid content returned
    mock_page.evaluate.return_value = "   " # less than 100 chars, or empty

    with patch.object(enrichment_engine, '_get_browser', return_value=mock_browser), \
         patch("src.scrapers.enrichment_engine._install_ssrf_route_guard", new_callable=AsyncMock), \
         patch("src.scrapers.enrichment_engine.assert_safe_url", new_callable=AsyncMock):

        result = await enrichment_engine.enrich_lead(lead)

        assert result["enrichment_status"] == "FAILED_NO_CONTENT"
        assert "company_name" not in result
