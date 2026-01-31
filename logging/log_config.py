"""Logging configuration using loguru."""

import os
import sys
from typing import Optional, Dict, Any
from loguru import logger

from config.settings import get_config, LoggingConfig


class InterceptHandler:
    """Intercept standard library logging and redirect to loguru."""
    
    def write(self, message: str) -> None:
        """Write message to loguru."""
        if message.strip():
            logger.opt(depth=1).info(message.strip())
    
    def flush(self) -> None:
        """Flush handler."""
        pass


def setup_logging(config: Optional[LoggingConfig] = None) -> None:
    """Configure loguru logging.
    
    Args:
        config: Logging configuration. If None, uses global config.
    """
    if config is None:
        config = get_config().logging
    
    # Remove default handler
    logger.remove()
    
    # Create log directory if needed
    log_dir = os.path.dirname(config.file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    # Add console handler
    logger.add(
        sys.stdout,
        level=config.level,
        format=config.format,
        colorize=True,
        enqueue=True,
        backtrace=True,
        diagnose=True
    )
    
    # Add file handler
    logger.add(
        config.file_path,
        level=config.level,
        format=config.format,
        rotation=config.rotation,
        retention=config.retention,
        enqueue=True,
        backtrace=True,
        diagnose=True,
        compression="zip"
    )
    
    # Intercept standard library logging
    import logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    logger.info("Logging configured successfully")


def get_logger(name: Optional[str] = None):
    """Get a logger instance with optional name binding.
    
    Args:
        name: Logger name/context
        
    Returns:
        Configured logger instance
    """
    if name:
        return logger.bind(name=name)
    return logger


class TradingLogger:
    """Structured logging for trading events."""
    
    def __init__(self, name: str):
        self.logger = get_logger(name)
    
    def trade(self, action: str, symbol: str, quantity: float, price: float, 
              order_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        """Log a trade event."""
        self.logger.info(
            f"TRADE: {action} {quantity} {symbol} @ {price}",
            extra={
                "event_type": "trade",
                "action": action,
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "order_id": order_id,
                "metadata": metadata or {}
            }
        )
    
    def signal(self, strategy: str, symbol: str, action: str, confidence: float,
               metadata: Optional[Dict[str, Any]] = None):
        """Log a strategy signal."""
        self.logger.info(
            f"SIGNAL: {strategy} -> {action} {symbol} (confidence: {confidence:.2%})",
            extra={
                "event_type": "signal",
                "strategy": strategy,
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "metadata": metadata or {}
            }
        )
    
    def risk_event(self, event_type: str, details: str, 
                   metrics: Optional[Dict[str, Any]] = None):
        """Log a risk management event."""
        self.logger.warning(
            f"RISK: {event_type} - {details}",
            extra={
                "event_type": "risk",
                "risk_type": event_type,
                "details": details,
                "metrics": metrics or {}
            }
        )
    
    def order_status(self, order_id: str, status: str, 
                     filled_qty: Optional[float] = None,
                     avg_price: Optional[float] = None):
        """Log order status update."""
        self.logger.info(
            f"ORDER: {order_id} -> {status}",
            extra={
                "event_type": "order_status",
                "order_id": order_id,
                "status": status,
                "filled_qty": filled_qty,
                "avg_price": avg_price
            }
        )
    
    def market_data(self, symbol: str, price: float, 
                    metadata: Optional[Dict[str, Any]] = None):
        """Log market data (rate-limited to avoid spam)."""
        self.logger.debug(
            f"DATA: {symbol} @ {price}",
            extra={
                "event_type": "market_data",
                "symbol": symbol,
                "price": price,
                "metadata": metadata or {}
            }
        )
    
    def error(self, error_type: str, message: str, 
              exception: Optional[Exception] = None):
        """Log an error."""
        if exception:
            self.logger.exception(f"ERROR: {error_type} - {message}")
        else:
            self.logger.error(f"ERROR: {error_type} - {message}")


# Initialize logging on module import
setup_logging()
