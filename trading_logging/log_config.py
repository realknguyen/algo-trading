"""Logging configuration using loguru."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

try:
    from loguru import logger as loguru_logger
except ImportError:  # pragma: no cover - exercised in minimal environments
    loguru_logger = None

from config.settings import LoggingConfig, get_config


class InterceptHandler(logging.Handler):
    """Intercept standard library logging and redirect to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message:
            return

        if loguru_logger is not None:
            try:
                level = loguru_logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            loguru_logger.opt(exception=record.exc_info, depth=6).log(level, message)
            return

        sys.__stderr__.write(f"{message}\n")


_logging_configured = False


def setup_logging(config: Optional[LoggingConfig] = None) -> None:
    """Configure loguru logging once per process."""
    global _logging_configured
    if _logging_configured:
        return

    if config is None:
        config = get_config().logging

    if loguru_logger is not None:
        loguru_logger.remove()

        log_dir = os.path.dirname(config.file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        loguru_logger.add(
            sys.stdout,
            level=config.level,
            format=config.format,
            colorize=True,
            enqueue=True,
            backtrace=True,
            diagnose=True,
        )

        loguru_logger.add(
            config.file_path,
            level=config.level,
            format=config.format,
            rotation=config.rotation,
            retention=config.retention,
            enqueue=True,
            backtrace=True,
            diagnose=True,
            compression="zip",
        )

        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    else:
        log_dir = os.path.dirname(config.file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(
            level=getattr(logging, config.level.upper(), logging.INFO),
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.file_path),
            ],
            force=True,
        )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _logging_configured = True
    get_logger("trading").info("Logging configured successfully")


def get_logger(name: Optional[str] = None):
    """Get a logger instance with optional name binding."""
    if loguru_logger is not None:
        if name:
            return loguru_logger.bind(name=name)
        return loguru_logger
    return logging.getLogger(name or "trading")


class TradingLogger:
    """Structured logging for trading events."""

    def __init__(self, name: str):
        self.logger = get_logger(name)

    def trade(
        self,
        action: str,
        symbol: str,
        quantity: float,
        price: float,
        order_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger.info(
            f"TRADE: {action} {quantity} {symbol} @ {price}",
            extra={
                "event_type": "trade",
                "action": action,
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "order_id": order_id,
                "metadata": metadata or {},
            },
        )

    def signal(
        self,
        strategy: str,
        symbol: str,
        action: str,
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger.info(
            f"SIGNAL: {strategy} -> {action} {symbol} (confidence: {confidence:.2%})",
            extra={
                "event_type": "signal",
                "strategy": strategy,
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "metadata": metadata or {},
            },
        )

    def risk_event(
        self,
        event_type: str,
        details: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger.warning(
            f"RISK: {event_type} - {details}",
            extra={
                "event_type": "risk",
                "risk_type": event_type,
                "details": details,
                "metrics": metrics or {},
            },
        )

    def order_status(
        self,
        order_id: str,
        status: str,
        filled_qty: Optional[float] = None,
        avg_price: Optional[float] = None,
    ) -> None:
        self.logger.info(
            f"ORDER: {order_id} -> {status}",
            extra={
                "event_type": "order_status",
                "order_id": order_id,
                "status": status,
                "filled_qty": filled_qty,
                "avg_price": avg_price,
            },
        )

    def market_data(
        self,
        symbol: str,
        price: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger.debug(
            f"DATA: {symbol} @ {price}",
            extra={
                "event_type": "market_data",
                "symbol": symbol,
                "price": price,
                "metadata": metadata or {},
            },
        )

    def error(
        self,
        error_type: str,
        message: str,
        exception: Optional[Exception] = None,
    ) -> None:
        if exception:
            self.logger.exception(f"ERROR: {error_type} - {message}")
            return
        self.logger.error(f"ERROR: {error_type} - {message}")
