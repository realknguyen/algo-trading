"""Tests for logging compatibility helpers."""

import logging

from trading_logging.log_config import InterceptHandler


def test_intercept_handler_is_stdlib_handler():
    """InterceptHandler should be compatible with logging.basicConfig handlers."""
    handler = InterceptHandler()

    assert isinstance(handler, logging.Handler)
    assert hasattr(handler, "formatter")
