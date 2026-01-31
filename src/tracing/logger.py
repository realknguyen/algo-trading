"""
Contextual Logging for Distributed Tracing.

Provides structured JSON logging with automatic request context injection.
Tracks correlation IDs across async boundaries.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Union

from .context import (
    ContextManager,
    RequestContext,
    get_current_context,
    ensure_context,
)


class ContextualLogRecord(logging.LogRecord):
    """
    Custom LogRecord that includes request context information.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add context fields
        context = get_current_context()
        if context:
            self.request_id = context.request_id
            self.trace_id = context.trace_id
            self.span_id = context.span_id
            self.parent_span_id = context.parent_span_id
        else:
            self.request_id = None
            self.trace_id = None
            self.span_id = None
            self.parent_span_id = None


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    
    Output format:
    {
        "timestamp": "2024-01-15T12:30:45.123456Z",
        "level": "INFO",
        "logger": "myapp.module",
        "message": "Something happened",
        "request_id": "abc123",
        "trace_id": "def456",
        "span_id": "ghi789",
        "extra": { ... }
    }
    """
    
    def __init__(
        self,
        include_context: bool = True,
        include_stacktrace: bool = True,
        default_fields: Optional[Dict[str, Any]] = None
    ):
        super().__init__()
        self.include_context = include_context
        self.include_stacktrace = include_stacktrace
        self.default_fields = default_fields or {}
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add context fields
        if self.include_context:
            if hasattr(record, "request_id") and record.request_id:
                log_obj["request_id"] = record.request_id
                log_obj["trace_id"] = record.trace_id
                log_obj["span_id"] = record.span_id
                if record.parent_span_id:
                    log_obj["parent_span_id"] = record.parent_span_id
        
        # Add exception info
        if record.exc_info and self.include_stacktrace:
            log_obj["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stacktrace": traceback.format_exception(*record.exc_info)
            }
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "request_id",
                "trace_id", "span_id", "parent_span_id", "message", "asctime"
            }:
                if not key.startswith("_"):
                    log_obj[key] = value
        
        # Add default fields
        log_obj.update(self.default_fields)
        
        return json.dumps(log_obj, default=str)


class ContextualLogger:
    """
    Logger wrapper that auto-injects request context into logs.
    
    Usage:
        logger = ContextualLogger("myapp")
        logger.info("Something happened", extra={"user_id": "123"})
    
    Or with context manager:
        with logger.context_span("processing_order"):
            logger.info("Processing started")
    """
    
    def __init__(
        self,
        name: str,
        level: int = logging.INFO,
        handler: Optional[logging.Handler] = None,
        json_format: bool = True
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        
        # Remove existing handlers to avoid duplicates
        self.logger.handlers = []
        
        # Add handler
        if handler is None:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)
        
        # Set formatter
        if json_format:
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s "
                "[request_id=%(request_id)s trace_id=%(trace_id)s]"
            ))
        
        self.logger.addHandler(handler)
        self._json_format = json_format
    
    def _log(
        self,
        level: int,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Optional[Union[bool, tuple]] = None,
        **kwargs
    ) -> None:
        """Internal log method with context injection."""
        context = get_current_context()
        
        # Build extra dict with context
        log_extra: Dict[str, Any] = {}
        if context:
            log_extra["request_id"] = context.request_id
            log_extra["trace_id"] = context.trace_id
            log_extra["span_id"] = context.span_id
            log_extra["parent_span_id"] = context.parent_span_id
        
        if extra:
            log_extra.update(extra)
        
        self.logger.log(level, msg, *args, extra=log_extra, exc_info=exc_info, **kwargs)
    
    def debug(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, msg, *args, extra=extra, **kwargs)
    
    def info(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """Log info message."""
        self._log(logging.INFO, msg, *args, extra=extra, **kwargs)
    
    def warning(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """Log warning message."""
        self._log(logging.WARNING, msg, *args, extra=extra, **kwargs)
    
    def error(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """Log error message."""
        self._log(logging.ERROR, msg, *args, extra=extra, **kwargs)
    
    def exception(
        self,
        msg: str,
        *args,
        extra: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> None:
        """Log exception with traceback."""
        self._log(logging.ERROR, msg, *args, extra=extra, exc_info=True, **kwargs)
    
    def critical(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        """Log critical message."""
        self._log(logging.CRITICAL, msg, *args, extra=extra, **kwargs)
    
    @contextmanager
    def context_span(
        self,
        span_name: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Context manager for creating a logging span.
        
        Usage:
            with logger.context_span("process_payment"):
                process_payment()
        """
        from .context import ContextScope
        
        start_time = datetime.utcnow()
        extra_meta = {**(metadata or {}), "span_name": span_name}
        
        with ContextScope(span_name=span_name, metadata=extra_meta) as ctx:
            self.info(f"Span '{span_name}' started")
            try:
                yield ctx
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                self.info(f"Span '{span_name}' completed", extra={"elapsed_ms": elapsed})
            except Exception as e:
                elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
                self.exception(
                    f"Span '{span_name}' failed",
                    extra={"elapsed_ms": elapsed, "error": str(e)}
                )
                raise
    
    def bind(self, **kwargs) -> BoundContextualLogger:
        """Create a bound logger with preset fields."""
        return BoundContextualLogger(self, kwargs)


class BoundContextualLogger:
    """
    Logger with preset fields bound to it.
    
    Usage:
        bound_logger = logger.bind(user_id="123", action="create_order")
        bound_logger.info("Order created")  # Includes user_id and action
    """
    
    def __init__(self, parent: ContextualLogger, bound_fields: Dict[str, Any]):
        self._parent = parent
        self._bound_fields = bound_fields
    
    def _merge_extra(self, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge bound fields with provided extra."""
        if extra:
            return {**self._bound_fields, **extra}
        return self._bound_fields.copy()
    
    def debug(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.debug(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def info(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.info(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def warning(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.warning(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def error(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.error(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def exception(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.exception(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def critical(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._parent.critical(msg, *args, extra=self._merge_extra(extra), **kwargs)
    
    def bind(self, **kwargs) -> BoundContextualLogger:
        """Create new bound logger with additional fields."""
        return BoundContextualLogger(self._parent, {**self._bound_fields, **kwargs})


def log_with_context(
    level: int = logging.INFO,
    msg: str = "",
    extra: Optional[Dict[str, Any]] = None
) -> None:
    """
    Convenience function to log with current context.
    
    Usage:
        log_with_context(logging.INFO, "Processing item", extra={"item_id": "123"})
    """
    context = get_current_context()
    log_extra = {}
    
    if context:
        log_extra["request_id"] = context.request_id
        log_extra["trace_id"] = context.trace_id
        log_extra["span_id"] = context.span_id
    
    if extra:
        log_extra.update(extra)
    
    logging.log(level, msg, extra=log_extra)


def traced(
    logger: Optional[ContextualLogger] = None,
    span_name: Optional[str] = None,
    log_args: bool = False,
    log_result: bool = False
) -> Callable:
    """
    Decorator to trace function execution with logging.
    
    Usage:
        @traced()
        def my_function(x, y):
            return x + y
        
        @traced(span_name="custom_name", log_args=True)
        async def async_function(data):
            return process(data)
    """
    def decorator(func: Callable) -> Callable:
        nonlocal span_name
        if span_name is None:
            span_name = func.__name__
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = logger or ContextualLogger(func.__module__)
            
            with log.context_span(span_name):
                if log_args:
                    log.debug(f"Args: {args}, Kwargs: {kwargs}")
                
                try:
                    result = func(*args, **kwargs)
                    if log_result:
                        log.debug(f"Result: {result}")
                    return result
                except Exception:
                    log.exception(f"Exception in {span_name}")
                    raise
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            log = logger or ContextualLogger(func.__module__)
            
            with log.context_span(span_name):
                if log_args:
                    log.debug(f"Args: {args}, Kwargs: {kwargs}")
                
                try:
                    result = await func(*args, **kwargs)
                    if log_result:
                        log.debug(f"Result: {result}")
                    return result
                except Exception:
                    log.exception(f"Exception in {span_name}")
                    raise
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper
    
    return decorator


# Import for decorator
import asyncio


def setup_logging(
    level: int = logging.INFO,
    json_format: bool = True,
    include_context: bool = True
) -> None:
    """
    Setup root logging configuration with contextual support.
    
    Args:
        level: Logging level
        json_format: Use JSON formatter
        include_context: Include request context in logs
    """
    # Set custom LogRecord factory for context injection
    logging.setLogRecordFactory(ContextualLogRecord)
    
    # Configure root handler
    root = logging.getLogger()
    root.setLevel(level)
    
    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Add new handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    
    if json_format:
        handler.setFormatter(JSONFormatter(include_context=include_context))
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
    
    root.addHandler(handler)


# Convenience function to get a logger
def get_logger(
    name: str,
    level: int = logging.INFO,
    json_format: bool = True
) -> ContextualLogger:
    """Get a contextual logger."""
    return ContextualLogger(name, level=level, json_format=json_format)
