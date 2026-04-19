"""
Distributed Tracing and Request Correlation Module.

Provides end-to-end request tracking across async boundaries with:
- Request context management
- ID generation (UUID7, nanoid)
- W3C/B3/Jaeger propagation formats
- Contextual structured logging
- HTTP client integration

Quick Start:
    from tracing import ContextManager, get_logger

    # Create context
    ctx = ContextManager.create_root()

    # Log with automatic context injection
    logger = get_logger("myapp")
    logger.info("Request processed")

    # HTTP client with auto-tracing
    from tracing.http import TracedHTTPClient
    async with TracedHTTPClient() as client:
        response = await client.get("https://api.example.com/data")
"""

from .context import (
    AsyncContextScope,
    ContextManager,
    ContextScope,
    RequestContext,
    ensure_context,
    get_current_context,
)
from .generator import (
    IDFormatter,
    RequestIDGenerator,
    generate_nanoid,
    generate_request_id,
    generate_span_id,
    generate_trace_id,
)
from .logger import (
    BoundContextualLogger,
    ContextualLogger,
    JSONFormatter,
    get_logger,
    log_with_context,
    setup_logging,
    traced,
)
from .propagation import (
    B3Propagator,
    ContextPropagator,
    JaegerPropagator,
    TraceHeaders,
    W3CTraceContext,
    default_propagator,
    extract_context,
    extract_or_create,
    inject_context,
)

__version__ = "1.0.0"

__all__ = [
    # Context
    "AsyncContextScope",
    "ContextManager",
    "ContextScope",
    "RequestContext",
    "ensure_context",
    "get_current_context",
    # Generator
    "IDFormatter",
    "RequestIDGenerator",
    "generate_nanoid",
    "generate_request_id",
    "generate_span_id",
    "generate_trace_id",
    # Logger
    "BoundContextualLogger",
    "ContextualLogger",
    "JSONFormatter",
    "get_logger",
    "log_with_context",
    "setup_logging",
    "traced",
    # Propagation
    "B3Propagator",
    "ContextPropagator",
    "JaegerPropagator",
    "TraceHeaders",
    "W3CTraceContext",
    "default_propagator",
    "extract_context",
    "extract_or_create",
    "inject_context",
]
