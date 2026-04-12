"""Compatibility re-export for top-level logging imports."""

from trading_logging.log_config import (
    InterceptHandler,
    TradingLogger,
    get_logger,
    setup_logging,
)

setup_logging()

__all__ = [
    "InterceptHandler",
    "TradingLogger",
    "get_logger",
    "setup_logging",
]
