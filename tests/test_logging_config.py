"""Tests for the logging configuration module."""
import logging
import os
import pytest
from unittest.mock import patch, MagicMock


def test_setup_logging(tmp_path, monkeypatch):
    """Test that setup_logging configures the root logger correctly."""
    from src.utils.logging_config import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    log_file = tmp_path / "app.log"
    monkeypatch.setenv("LOG_FILE", str(log_file))
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    try:
        root.handlers.clear()
        root.setLevel(logging.WARNING)

        setup_logging()

        assert root.level == logging.INFO, \
            f"Root logger level should be INFO (20), got {root.level}"

        assert len(root.handlers) == 2, \
            f"Expected 2 handlers (stream + file), got {len(root.handlers)}: {root.handlers}"

        handler_types = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in handler_types
        assert "RotatingFileHandler" in handler_types

        for name in ("httpx", "httpcore", "urllib3", "playwright", "aiohttp"):
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        for h in root.handlers[:]:
            if hasattr(h, 'close'):
                h.close()
        root.handlers = original_handlers
        root.level = original_level


def test_get_logger():
    """Test that get_logger returns a properly named logger."""
    from src.utils.logging_config import get_logger

    logger = get_logger("test_verification_module")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "test_verification_module"

    # Same name returns same instance
    assert get_logger("test_verification_module") is logger

    # Different name returns different logger
    other = get_logger("other_verification_module")
    assert other.name == "other_verification_module"
    assert other is not logger
