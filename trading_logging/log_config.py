"""Canonical logging helpers for trading runtime and compatibility imports."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Dict, Optional

from config.settings import LoggingConfig, get_config

_loguru_logger: Any | None

try:
    from loguru import logger as _imported_loguru_logger
except ImportError:  # pragma: no cover - environment-specific fallback
    _loguru_logger = None
else:
    _loguru_logger = _imported_loguru_logger

SENSITIVE_FIELD_NAMES = (
    "api_key",
    "api_secret",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "token",
    "authorization",
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(authorization\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(api[_ -]?secret\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(passphrase\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(private[_ -]?key\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
)

_LOGGING_CONFIGURED = False


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_FIELD_NAMES)


def sanitize_text(value: str) -> str:
    """Redact common secret-looking fragments from a string message."""
    redacted = value
    for pattern in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub(r"\1***", redacted)
    return redacted


def sanitize_for_logging(value: Any) -> Any:
    """Recursively redact sensitive values before they are logged."""
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_key(str(key)) else sanitize_for_logging(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        items = [sanitize_for_logging(item) for item in value]
        if isinstance(value, tuple):
            return tuple(items)
        if isinstance(value, set):
            return set(items)
        return items
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def build_loguru_options(
    config: LoggingConfig,
    *,
    debug_diagnostics: bool = False,
) -> dict[str, Any]:
    """Build the shared loguru sink options."""
    return {
        "level": config.level,
        "format": config.format,
        "enqueue": True,
        # Secret-bearing auth/signing code should not dump local state by default.
        "backtrace": debug_diagnostics,
        "diagnose": debug_diagnostics,
    }


def is_logging_configured() -> bool:
    """Return whether logging has already been configured."""
    return _LOGGING_CONFIGURED


def reset_logging_state() -> None:
    """Reset logging configuration for tests."""
    global _LOGGING_CONFIGURED
    if _loguru_logger is not None:
        _loguru_logger.remove()
    _LOGGING_CONFIGURED = False


class InterceptHandler(logging.Handler):
    """Forward stdlib log records into loguru when available."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin adapter
        if _loguru_logger is None:
            return

        try:
            level: Any = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        _loguru_logger.opt(
            depth=6,
            exception=record.exc_info,
        ).log(level, sanitize_text(record.getMessage()))


def setup_logging(
    config: Optional[LoggingConfig] = None,
    *,
    debug_diagnostics: Optional[bool] = None,
    force: bool = False,
) -> None:
    """Configure application logging without import-time side effects."""
    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED and not force:
        return

    if config is None:
        config = get_config().logging

    if debug_diagnostics is None:
        debug_diagnostics = bool(config.debug_diagnostics)

    stdlib_level = getattr(logging, str(config.level).upper(), logging.INFO)

    if _loguru_logger is None:
        logging.basicConfig(
            level=stdlib_level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            force=True,
        )
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        _LOGGING_CONFIGURED = True
        return

    logger = _loguru_logger
    logger.remove()

    log_dir = os.path.dirname(config.file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    base_options = build_loguru_options(
        config,
        debug_diagnostics=debug_diagnostics,
    )

    logger.add(
        sys.stdout,
        colorize=True,
        **base_options,
    )
    logger.add(
        config.file_path,
        rotation=config.rotation,
        retention=config.retention,
        compression="zip",
        **base_options,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=stdlib_level, force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    _LOGGING_CONFIGURED = True
    logger.info("Logging configured successfully")


def get_logger(name: Optional[str] = None) -> Any:
    """Return a configured logger instance."""
    if not _LOGGING_CONFIGURED:
        setup_logging()

    if _loguru_logger is None:
        return logging.getLogger(name or "trading")

    if name:
        return _loguru_logger.bind(name=name)
    return _loguru_logger


class TradingLogger:
    """Structured logging helper for trading events."""

    def __init__(self, name: str):
        self.logger = get_logger(name)

    def _log(self, level: str, message: str, **extra: Any) -> None:
        safe_message = sanitize_text(message)
        safe_extra = sanitize_for_logging(extra)

        if _loguru_logger is None:
            stdlib_level = getattr(logging, level.upper(), logging.INFO)
            self.logger.log(stdlib_level, safe_message, extra=safe_extra)
            return

        self.logger.bind(**safe_extra).log(level.upper(), safe_message)

    def debug(self, message: str, **extra: Any) -> None:
        """Emit a debug log with sanitized structured context."""
        self._log("debug", message, **extra)

    def info(self, message: str, **extra: Any) -> None:
        """Emit an info log with sanitized structured context."""
        self._log("info", message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        """Emit a warning log with sanitized structured context."""
        self._log("warning", message, **extra)

    def trade(
        self,
        action: str,
        symbol: str,
        quantity: float,
        price: float,
        order_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._log(
            "info",
            f"TRADE: {action} {quantity} {symbol} @ {price}",
            event_type="trade",
            action=action,
            symbol=symbol,
            quantity=quantity,
            price=price,
            order_id=order_id,
            metadata=metadata or {},
        )

    def signal(
        self,
        strategy: str,
        symbol: str,
        action: str,
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._log(
            "info",
            f"SIGNAL: {strategy} -> {action} {symbol} (confidence: {confidence:.2%})",
            event_type="signal",
            strategy=strategy,
            symbol=symbol,
            action=action,
            confidence=confidence,
            metadata=metadata or {},
        )

    def risk_event(
        self,
        event_type: str,
        details: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._log(
            "warning",
            f"RISK: {event_type} - {details}",
            event_type="risk",
            risk_type=event_type,
            details=details,
            metrics=metrics or {},
        )

    def order_status(
        self,
        order_id: str,
        status: str,
        filled_qty: Optional[float] = None,
        avg_price: Optional[float] = None,
    ) -> None:
        self._log(
            "info",
            f"ORDER: {order_id} -> {status}",
            event_type="order_status",
            order_id=order_id,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
        )

    def market_data(
        self,
        symbol: str,
        price: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._log(
            "debug",
            f"DATA: {symbol} @ {price}",
            event_type="market_data",
            symbol=symbol,
            price=price,
            metadata=metadata or {},
        )

    def error(
        self,
        error_type: str,
        message: str,
        exception: Optional[Exception] = None,
        **extra: Any,
    ) -> None:
        safe_message = sanitize_text(f"ERROR: {error_type} - {message}")
        safe_extra = sanitize_for_logging(extra)

        if _loguru_logger is None:
            self.logger.error(
                safe_message,
                exc_info=exception,
                extra={"error_type": error_type, **safe_extra},
            )
            return

        bound = self.logger.bind(error_type=error_type, **safe_extra)
        if exception is not None:
            bound.opt(exception=exception).error(safe_message)
        else:
            bound.error(safe_message)
