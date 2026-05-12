"""Tests for the logging configuration module."""
import logging
import os
import pytest
from unittest.mock import patch, MagicMock


def test_setup_logging(tmp_path):
    """Test that setup_logging configures the root logger correctly."""
    # We need to import AFTER patching to ensure our mocks take effect,
    # but the module is already loaded. Instead, test the actual effects.
    from src.utils.logging_config import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    try:
        root.handlers.clear()
        root.setLevel(logging.WARNING)

        # Redirect log output to tmp_path
        log_dir = os.path.join(str(tmp_path), "logs")

        # Patch the __file__-relative log directory calculation
        with patch("src.utils.logging_config.os.path.dirname", return_value=str(tmp_path)):
            setup_logging()

        # 1. Root logger should be DEBUG
        assert root.level == logging.DEBUG, \
            f"Root logger level should be DEBUG (10), got {root.level}"

        # 2. Should have 2 handlers (StreamHandler + RotatingFileHandler)
        assert len(root.handlers) == 2, \
            f"Expected 2 handlers, got {len(root.handlers)}: {root.handlers}"

        # 3. Check handler types
        handler_types = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in handler_types
        assert "RotatingFileHandler" in handler_types

        # 4. Third-party loggers suppressed
        for name in ("httpx", "httpcore", "urllib3", "playwright", "aiohttp"):
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        # Clean up: close any file handlers we opened and restore state
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
