import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
def discovery_engine():
    # Import inside to delay SupabaseHelper/AgenticRouter init
    with patch("src.scrapers.discovery_engine.SupabaseHelper"), \
         patch("src.scrapers.discovery_engine.AgenticRouter"):
        from src.scrapers.discovery_engine import DiscoveryEngine
        return DiscoveryEngine()

@pytest.fixture
def mock_playwright():
    with patch("src.scrapers.discovery_engine.async_playwright") as mock_pw_context:
        mock_pw = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        # async with async_playwright() context manager
        mock_pw_context.return_value.__aenter__.return_value = mock_pw
        yield mock_pw_context, mock_pw, mock_browser, mock_context, mock_page

@pytest.mark.asyncio
async def test_find_leads_happy_path(discovery_engine, mock_playwright):
    _, _, _, _, mock_page = mock_playwright

    # Mock search input
    query = "dentist"
    location = "sarajevo"

    # Mock containers and extraction
    mock_container_1 = AsyncMock()
    mock_container_2 = AsyncMock()

    mock_page.query_selector_all.return_value = [mock_container_1, mock_container_2]

    lead_1 = {"name": "Dentist 1", "unique_key": "key1"}
    lead_2 = {"name": "Dentist 2", "unique_key": "key2"}

    # Mock the internal extraction method
    with patch.object(discovery_engine, "_extract_lead_data", new_callable=AsyncMock) as mock_extract:
        mock_extract.side_effect = [lead_1, lead_2]

        leads = await discovery_engine.find_leads(query=query, location=location, max_results=50)

        assert len(leads) == 2
        assert leads[0] == lead_1
        assert leads[1] == lead_2
        assert mock_page.goto.call_count == 1

        # Check URL contains urlencoded query
        url = mock_page.goto.call_args[0][0]
        assert "dentist+in+sarajevo" in url

@pytest.mark.asyncio
async def test_find_leads_deduplication(discovery_engine, mock_playwright):
    _, _, _, _, mock_page = mock_playwright

    mock_container_1 = AsyncMock()
    mock_container_2 = AsyncMock()

    mock_page.query_selector_all.return_value = [mock_container_1, mock_container_2]

    # Both leads have the same unique_key
    lead_1 = {"name": "Dentist 1", "unique_key": "dup_key"}
    lead_2 = {"name": "Dentist 1 Dupe", "unique_key": "dup_key"}

    with patch.object(discovery_engine, "_extract_lead_data", new_callable=AsyncMock) as mock_extract:
        mock_extract.side_effect = [lead_1, lead_2]

        leads = await discovery_engine.find_leads(query="test", max_results=50)

        # Should be deduplicated to 1 lead (the last one wins in the loop)
        assert len(leads) == 1
        assert leads[0] == lead_2

@pytest.mark.asyncio
async def test_find_leads_no_containers(discovery_engine, mock_playwright):
    _, _, _, _, mock_page = mock_playwright

    # Return empty list of containers
    mock_page.query_selector_all.return_value = []

    leads = await discovery_engine.find_leads(query="empty", max_results=50)

    assert len(leads) == 0

@pytest.mark.asyncio
async def test_find_leads_timeout_on_goto(discovery_engine, mock_playwright):
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    _, _, mock_browser, _, mock_page = mock_playwright

    # Simulate a PlaywrightTimeoutError on goto
    mock_page.goto.side_effect = PlaywrightTimeoutError("Timeout")

    leads = await discovery_engine.find_leads(query="timeout", max_results=50)

    # Since it raises TimeoutError inside a try-except, it's caught and returns empty leads
    assert len(leads) == 0
    # Make sure browser is closed even on error
    assert mock_browser.close.call_count == 1

@pytest.mark.asyncio
async def test_find_leads_max_results_limit(discovery_engine, mock_playwright):
    _, _, _, _, mock_page = mock_playwright

    # Create 10 fake containers
    containers = [AsyncMock() for _ in range(10)]
    mock_page.query_selector_all.return_value = containers

    with patch.object(discovery_engine, "_extract_lead_data", new_callable=AsyncMock) as mock_extract:
        # Mock extract to just return a dummy lead based on call order
        def extract_side_effect(page, container):
            return {"name": "Lead", "unique_key": str(id(container))}
        mock_extract.side_effect = extract_side_effect

        leads = await discovery_engine.find_leads(query="limit test", max_results=3)

        # Should only extract 3 leads
        assert len(leads) == 3
        assert mock_extract.call_count == 3
