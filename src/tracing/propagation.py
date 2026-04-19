"""
Context Propagation for Distributed Tracing.

Supports W3C Trace Context, Jaeger, and B3 propagation formats
for extracting and injecting trace context across HTTP boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .context import RequestContext
from .generator import RequestIDGenerator


@dataclass
class TraceHeaders:
    """Container for trace context HTTP headers."""

    traceparent: Optional[str] = None
    tracestate: Optional[str] = None
    jaeger_trace_id: Optional[str] = None
    jaeger_span_id: Optional[str] = None
    jaeger_parent_id: Optional[str] = None
    jaeger_sampled: Optional[str] = None
    b3_trace_id: Optional[str] = None
    b3_span_id: Optional[str] = None
    b3_parent_span_id: Optional[str] = None
    b3_sampled: Optional[str] = None
    b3_flags: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None


class W3CTraceContext:
    """
    W3C Trace Context implementation.

    Format: traceparent: version-trace_id-parent_id-trace_flags
    Example: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01

    Spec: https://www.w3.org/TR/trace-context/
    """

    VERSION = "00"
    HEADER_TRACEPARENT = "traceparent"
    HEADER_TRACESTATE = "tracestate"

    # Regex for validating traceparent
    # version(2)-trace_id(32)-parent_id(16)-flags(2)
    TRACEPARENT_REGEX = re.compile(
        r"^(?P<version>[0-9a-f]{2})-"
        r"(?P<trace_id>[0-9a-f]{32})-"
        r"(?P<parent_id>[0-9a-f]{16})-"
        r"(?P<flags>[0-9a-f]{2})$"
    )

    @classmethod
    def inject(
        cls, context: RequestContext, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Inject W3C trace context into headers.

        Args:
            context: Request context to inject
            headers: Existing headers dict (creates new if None)

        Returns:
            Headers dict with trace context
        """
        if headers is None:
            headers = {}

        # Format: version-trace_id-parent_id-flags
        # flags: 00 = not sampled, 01 = sampled
        traceparent = (
            f"{cls.VERSION}-"
            f"{context.trace_id:0<32}"[:32] + "-"
            f"{context.span_id:0<16}"[:16] + "-"
            "01"  # sampled
        )

        headers[cls.HEADER_TRACEPARENT] = traceparent

        # Add tracestate if metadata present
        if context.metadata:
            tracestate = cls._build_tracestate(context)
            if tracestate:
                headers[cls.HEADER_TRACESTATE] = tracestate

        return headers

    @classmethod
    def extract(cls, headers: Dict[str, str]) -> Optional[RequestContext]:
        """
        Extract W3C trace context from headers.

        Args:
            headers: HTTP headers dict

        Returns:
            RequestContext if valid traceparent found, None otherwise
        """
        traceparent = headers.get(cls.HEADER_TRACEPARENT)
        if not traceparent:
            return None

        match = cls.TRACEPARENT_REGEX.match(traceparent)
        if not match:
            return None

        groups = match.groupdict()
        trace_id = groups["trace_id"]
        parent_id = groups["parent_id"]

        # Parse tracestate
        metadata = cls._parse_tracestate(headers.get(cls.HEADER_TRACESTATE, ""))

        return RequestContext(
            request_id=metadata.get("request_id") or RequestIDGenerator.generate_request_id(),
            trace_id=trace_id,
            span_id=RequestIDGenerator.generate_span_id(),
            parent_span_id=parent_id,
            metadata=metadata,
        )

    @classmethod
    def _build_tracestate(cls, context: RequestContext) -> Optional[str]:
        """Build tracestate string from context metadata."""
        # Include relevant metadata in tracestate
        # Format: vendor1=key1,vendor2=key2
        entries = []

        if "request_id" in context.metadata:
            entries.append(f"reqid={context.metadata['request_id']}")

        if "service" in context.metadata:
            entries.append(f"svc={context.metadata['service']}")

        return ",".join(entries) if entries else None

    @classmethod
    def _parse_tracestate(cls, tracestate: str) -> Dict[str, str]:
        """Parse tracestate string into metadata dict."""
        metadata = {}

        for entry in tracestate.split(","):
            entry = entry.strip()
            if "=" in entry:
                key, value = entry.split("=", 1)
                # Map tracestate keys to metadata
                if key == "reqid":
                    metadata["request_id"] = value
                elif key == "svc":
                    metadata["service"] = value
                else:
                    metadata[key] = value

        return metadata


class JaegerPropagator:
    """
    Jaeger propagation format (Uber trace ID).

    Headers:
    - uber-trace-id: {trace-id}:{span-id}:{parent-span-id}:{flags}

    Format: {trace-id}:{span-id}:{parent-span-id}:{flags}
    Example: 4f6a7a3b2c1d4e5f6a7a8b9c0d1e2f3a:7a8b9c0d1e2f3a4b:0:1
    """

    HEADER_TRACE_ID = "uber-trace-id"
    HEADER_BAGGAGE_PREFIX = "uberctx-"

    @classmethod
    def inject(
        cls, context: RequestContext, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Inject Jaeger trace context into headers."""
        if headers is None:
            headers = {}

        # Format: trace-id:span-id:parent-span-id:flags
        trace_id = context.trace_id[:32].zfill(32)
        span_id = context.span_id[:16].zfill(16)
        parent_id = (context.parent_span_id or "0")[:16].zfill(16)
        flags = "1"  # sampled

        headers[cls.HEADER_TRACE_ID] = f"{trace_id}:{span_id}:{parent_id}:{flags}"

        # Inject baggage/metadata
        for key, value in context.metadata.items():
            headers[f"{cls.HEADER_BAGGAGE_PREFIX}{key}"] = str(value)

        return headers

    @classmethod
    def extract(cls, headers: Dict[str, str]) -> Optional[RequestContext]:
        """Extract Jaeger trace context from headers."""
        trace_header = headers.get(cls.HEADER_TRACE_ID)
        if not trace_header:
            return None

        parts = trace_header.split(":")
        if len(parts) != 4:
            return None

        trace_id, parent_id, _, flags = parts

        # Extract baggage
        metadata = {}
        for key, value in headers.items():
            if key.lower().startswith(cls.HEADER_BAGGAGE_PREFIX):
                baggage_key = key[len(cls.HEADER_BAGGAGE_PREFIX) :]
                metadata[baggage_key] = value

        return RequestContext(
            request_id=metadata.get("request_id") or RequestIDGenerator.generate_request_id(),
            trace_id=trace_id.lower(),
            span_id=RequestIDGenerator.generate_span_id(),
            parent_span_id=parent_id if parent_id != "0" else None,
            metadata=metadata,
        )


class B3Propagator:
    """
    B3 (Zipkin) propagation format.

    Single header:
    - b3: {trace-id}-{span-id}-{sampled}-{parent-span-id}

    Or multi-header:
    - X-B3-TraceId: {trace-id}
    - X-B3-SpanId: {span-id}
    - X-B3-ParentSpanId: {parent-span-id}
    - X-B3-Sampled: {0 or 1}
    - X-B3-Flags: {1 for debug}
    """

    HEADER_SINGLE = "b3"
    HEADER_TRACE_ID = "X-B3-TraceId"
    HEADER_SPAN_ID = "X-B3-SpanId"
    HEADER_PARENT_SPAN_ID = "X-B3-ParentSpanId"
    HEADER_SAMPLED = "X-B3-Sampled"
    HEADER_FLAGS = "X-B3-Flags"

    @classmethod
    def inject(
        cls,
        context: RequestContext,
        headers: Optional[Dict[str, str]] = None,
        single_header: bool = False,
    ) -> Dict[str, str]:
        """Inject B3 trace context into headers."""
        if headers is None:
            headers = {}

        trace_id = context.trace_id[:32].zfill(32)
        span_id = context.span_id[:16].zfill(16)
        parent_id = context.parent_span_id[:16].zfill(16) if context.parent_span_id else None

        if single_header:
            # Single header format
            parts = [trace_id, span_id, "1"]
            if parent_id:
                parts.append(parent_id)
            headers[cls.HEADER_SINGLE] = "-".join(parts)
        else:
            # Multi-header format
            headers[cls.HEADER_TRACE_ID] = trace_id
            headers[cls.HEADER_SPAN_ID] = span_id
            headers[cls.HEADER_SAMPLED] = "1"
            if parent_id:
                headers[cls.HEADER_PARENT_SPAN_ID] = parent_id

        return headers

    @classmethod
    def extract(cls, headers: Dict[str, str]) -> Optional[RequestContext]:
        """Extract B3 trace context from headers."""
        # Try single header first
        single = headers.get(cls.HEADER_SINGLE)
        if single:
            return cls._extract_single(single)

        # Try multi-header
        return cls._extract_multi(headers)

    @classmethod
    def _extract_single(cls, header: str) -> Optional[RequestContext]:
        """Extract from single B3 header."""
        parts = header.split("-")
        if len(parts) < 2:
            return None

        trace_id = parts[0].lower()
        parent_id = parts[1] if len(parts) > 1 else None

        return RequestContext(
            request_id=RequestIDGenerator.generate_request_id(),
            trace_id=trace_id,
            span_id=RequestIDGenerator.generate_span_id(),
            parent_span_id=parent_id if parent_id and parent_id != "0" else None,
        )

    @classmethod
    def _extract_multi(cls, headers: Dict[str, str]) -> Optional[RequestContext]:
        """Extract from multi-header B3 format."""
        trace_id = headers.get(cls.HEADER_TRACE_ID)
        if not trace_id:
            return None

        parent_id = headers.get(cls.HEADER_PARENT_SPAN_ID)

        return RequestContext(
            request_id=RequestIDGenerator.generate_request_id(),
            trace_id=trace_id.lower(),
            span_id=RequestIDGenerator.generate_span_id(),
            parent_span_id=parent_id,
        )


class ContextPropagator:
    """
    Unified context propagator supporting multiple formats.

    Priority for extraction: W3C -> B3 -> Jaeger
    """

    # Header names for extraction
    HEADER_REQUEST_ID = "X-Request-ID"
    HEADER_CORRELATION_ID = "X-Correlation-ID"

    def __init__(
        self,
        extract_formats: Optional[List[str]] = None,
        inject_formats: Optional[List[str]] = None,
    ):
        """
        Initialize propagator.

        Args:
            extract_formats: List of formats to try for extraction (priority order)
            inject_formats: List of formats to inject
        """
        self.extract_formats = extract_formats or ["w3c", "b3", "jaeger"]
        self.inject_formats = inject_formats or ["w3c"]

    def extract(self, headers: Dict[str, str]) -> Optional[RequestContext]:
        """
        Extract context from headers using configured formats.

        Args:
            headers: HTTP headers dict

        Returns:
            RequestContext if found, None otherwise
        """
        # Normalize header keys to lowercase for case-insensitive lookup
        normalized = {k.lower(): v for k, v in headers.items()}

        for fmt in self.extract_formats:
            context = None

            if fmt == "w3c":
                context = W3CTraceContext.extract(normalized)
            elif fmt == "b3":
                context = B3Propagator.extract(headers)  # B3 uses specific header names
            elif fmt == "jaeger":
                context = JaegerPropagator.extract(normalized)

            if context:
                # Check for request ID in custom headers
                if not context.metadata.get("request_id"):
                    req_id = headers.get(self.HEADER_REQUEST_ID) or headers.get(
                        self.HEADER_CORRELATION_ID
                    )
                    if req_id:
                        context = RequestContext(
                            request_id=req_id,
                            trace_id=context.trace_id,
                            span_id=context.span_id,
                            parent_span_id=context.parent_span_id,
                            start_time=context.start_time,
                            metadata={**context.metadata, "request_id": req_id},
                        )
                return context

        return None

    def inject(
        self, context: RequestContext, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Inject context into headers using configured formats.

        Args:
            context: Request context to inject
            headers: Existing headers dict

        Returns:
            Headers dict with trace context
        """
        if headers is None:
            headers = {}

        for fmt in self.inject_formats:
            if fmt == "w3c":
                W3CTraceContext.inject(context, headers)
            elif fmt == "b3":
                B3Propagator.inject(context, headers)
            elif fmt == "jaeger":
                JaegerPropagator.inject(context, headers)

        # Always add request ID header
        headers[self.HEADER_REQUEST_ID] = context.request_id
        headers[self.HEADER_CORRELATION_ID] = context.request_id

        return headers

    def extract_or_create(
        self, headers: Dict[str, str], metadata: Optional[Dict[str, Any]] = None
    ) -> RequestContext:
        """
        Extract context from headers or create new if not found.

        Args:
            headers: HTTP headers dict
            metadata: Optional metadata for new context

        Returns:
            RequestContext (existing or newly created)
        """
        context = self.extract(headers)
        if context:
            if metadata:
                return RequestContext(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    span_id=context.span_id,
                    parent_span_id=context.parent_span_id,
                    start_time=context.start_time,
                    metadata={**context.metadata, **metadata},
                )
            return context

        # Create new context
        return RequestContext(
            request_id=RequestIDGenerator.generate_request_id(),
            trace_id=RequestIDGenerator.generate_trace_id(),
            span_id=RequestIDGenerator.generate_span_id(),
            metadata=metadata or {},
        )


# Default propagator instance
default_propagator = ContextPropagator()


# Convenience functions
def extract_context(headers: Dict[str, str]) -> Optional[RequestContext]:
    """Extract context from headers using default propagator."""
    return default_propagator.extract(headers)


def inject_context(
    context: RequestContext, headers: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Inject context into headers using default propagator."""
    return default_propagator.inject(context, headers)


def extract_or_create(
    headers: Dict[str, str], metadata: Optional[Dict[str, Any]] = None
) -> RequestContext:
    """Extract or create context using default propagator."""
    return default_propagator.extract_or_create(headers, metadata)
