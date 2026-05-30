import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from src.scrapers.enrichment_engine import EnrichmentEngine

@pytest.fixture
def engine():
    return EnrichmentEngine()

@pytest.mark.asyncio
async def test_aclose_happy_path(engine):
    """Test that aclose properly stops playwright and closes the browser."""
    # Setup mocks
    engine._browser = AsyncMock()
    engine._pw = AsyncMock()

    await engine.aclose()

    assert engine._closed is True
    assert engine._browser is None
    assert engine._pw is None

@pytest.mark.asyncio
async def test_aclose_idempotent(engine):
    """Test that multiple calls to aclose are safe and return early."""
    engine._closed = True

    # Setup mocks
    mock_browser = AsyncMock()
    mock_pw = AsyncMock()
    engine._browser = mock_browser
    engine._pw = mock_pw

    await engine.aclose()

    assert engine._closed is True
    # The mocks should not be cleared since early return happens
    assert engine._browser is mock_browser
    assert engine._pw is mock_pw

    mock_browser.close.assert_not_called()
    mock_pw.stop.assert_not_called()

@pytest.mark.asyncio
async def test_aclose_browser_close_exception(engine):
    """Test that an exception during browser.close is caught and logged."""
    engine._browser = AsyncMock()
    engine._pw = AsyncMock()

    exception = Exception("Test Browser Close Error")
    engine._browser.close.side_effect = exception

    with patch("src.scrapers.enrichment_engine.logger") as mock_logger:
        # Keep a reference to the mocks before they are cleared
        pw_mock = engine._pw

        await engine.aclose()

        # Verify it still closes
        assert engine._closed is True
        assert engine._browser is None
        assert engine._pw is None

        mock_logger.warning.assert_any_call("Browser close raised: %s", exception)
        pw_mock.stop.assert_awaited_once()

@pytest.mark.asyncio
async def test_aclose_pw_stop_exception(engine):
    """Test that an exception during pw.stop is caught and logged."""
    engine._browser = AsyncMock()
    engine._pw = AsyncMock()

    exception = Exception("Test Playwright Stop Error")
    engine._pw.stop.side_effect = exception

    with patch("src.scrapers.enrichment_engine.logger") as mock_logger:
        # Keep a reference to the mocks before they are cleared
        browser_mock = engine._browser

        await engine.aclose()

        # Verify it still closes
        assert engine._closed is True
        assert engine._browser is None
        assert engine._pw is None

        mock_logger.warning.assert_any_call("Playwright stop raised: %s", exception)
        browser_mock.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_aclose_both_exceptions(engine):
    """Test that exceptions in both close operations are handled independently."""
    engine._browser = AsyncMock()
    engine._pw = AsyncMock()

    browser_exc = Exception("Browser exception")
    pw_exc = Exception("PW exception")

    engine._browser.close.side_effect = browser_exc
    engine._pw.stop.side_effect = pw_exc

    with patch("src.scrapers.enrichment_engine.logger") as mock_logger:
        await engine.aclose()

        assert engine._closed is True
        assert engine._browser is None
        assert engine._pw is None

        mock_logger.warning.assert_any_call("Browser close raised: %s", browser_exc)
        mock_logger.warning.assert_any_call("Playwright stop raised: %s", pw_exc)
