import logging
import os
import unittest.mock as mock
import pytest
from src.utils.logging_config import setup_logging, get_logger

@mock.patch("src.utils.logging_config.os.makedirs")
@mock.patch("src.utils.logging_config.RotatingFileHandler")
@mock.patch("src.utils.logging_config.logging.StreamHandler")
@mock.patch("src.utils.logging_config.logging.getLogger")
def test_setup_logging(mock_get_logger, mock_stream_handler, mock_rotating_file_handler, mock_makedirs):
    # Setup mocks
    mock_root_logger = mock.Mock()
    mock_get_logger.return_value = mock_root_logger

    # Mock individual loggers for third-party suppression
    mock_loggers = {name: mock.Mock() for name in ("httpx", "httpcore", "urllib3", "playwright", "aiohttp")}
    def get_logger_side_effect(name=None):
        if name is None:
            return mock_root_logger
        return mock_loggers.get(name, mock.Mock())

    mock_get_logger.side_effect = get_logger_side_effect

    # Call the function
    setup_logging()

    # Assertions
    # 1. Verify directory creation
    log_dir = os.path.join(os.path.dirname(os.path.abspath("src/utils/logging_config.py")), "..", "..", "logs")
    # Actually, the path is relative to the file.
    # Let's just check if makedirs was called with a path ending in 'logs'
    mock_makedirs.assert_called_once()
    args, kwargs = mock_makedirs.call_args
    assert args[0].endswith("logs")
    assert kwargs["exist_ok"] is True

    # 2. Verify root logger level
    mock_root_logger.setLevel.assert_any_call(logging.DEBUG)

    # 3. Verify handlers are added
    assert mock_root_logger.addHandler.call_count == 2

    # 4. Verify third-party loggers suppression
    for name in ("httpx", "httpcore", "urllib3", "playwright", "aiohttp"):
        mock_loggers[name].setLevel.assert_called_once_with(logging.WARNING)

def test_get_logger():
    with mock.patch("src.utils.logging_config.logging.getLogger") as mock_get_logger:
        mock_get_logger.return_value = "mock_logger"
        logger = get_logger("test_name")
        mock_get_logger.assert_called_once_with("test_name")
        assert logger == "mock_logger"
