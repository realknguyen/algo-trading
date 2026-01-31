"""
Tests for the tracing module.

Run with: pytest tests/test_tracing.py -v
"""

import asyncio
import json
import logging
import pytest
from unittest.mock import Mock, patch

from tracing import (
    AsyncContextScope,
    ContextManager,
    ContextScope,
    RequestContext,
    RequestIDGenerator,
    ContextPropagator,
    W3CTraceContext,
    B3Propagator,
    JaegerPropagator,
    get_logger,
    extract_context,
    inject_context,
    generate_request_id,
    generate_trace_id,
    generate_span_id,
)


class TestRequestIDGenerator:
    """Test ID generation."""
    
    def test_generate_request_id(self):
        """Test request ID generation."""
        req_id = generate_request_id()
        assert isinstance(req_id, str)
        assert len(req_id) > 0
    
    def test_generate_trace_id(self):
        """Test trace ID generation."""
        trace_id = generate_trace_id()
        assert isinstance(trace_id, str)
        assert len(trace_id) == 32  # W3C standard
    
    def test_generate_span_id(self):
        """Test span ID generation."""
        span_id = generate_span_id()
        assert isinstance(span_id, str)
        assert len(span_id) == 16  # W3C standard
    
    def test_generate_nanoid(self):
        """Test nanoid generation."""
        nanoid = RequestIDGenerator.generate_nanoid()
        assert isinstance(nanoid, str)
        assert len(nanoid) == 21  # Default size
    
    def test_generate_nanoid_custom_size(self):
        """Test nanoid with custom size."""
        nanoid = RequestIDGenerator.generate_nanoid(32)
        assert len(nanoid) == 32
    
    def test_generate_timestamp_id(self):
        """Test timestamp-based ID generation."""
        ts_id = RequestIDGenerator.generate_timestamp_id()
        assert "_" in ts_id
        parts = ts_id.split("_")
        assert len(parts) == 3  # date, time, random


class TestRequestContext:
    """Test RequestContext dataclass."""
    
    def test_create_context(self):
        """Test context creation."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        assert ctx.request_id == "req123"
        assert ctx.trace_id == "trace456"
        assert ctx.span_id == "span789"
        assert ctx.parent_span_id is None
    
    def test_child_context(self):
        """Test child context creation."""
        parent = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        child = parent.child("child_span")
        
        assert child.request_id == parent.request_id
        assert child.trace_id == parent.trace_id
        assert child.span_id != parent.span_id
        assert child.parent_span_id == parent.span_id
        assert child.metadata.get("span_name") == "child_span"
    
    def test_to_dict(self):
        """Test context serialization."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789",
            metadata={"key": "value"}
        )
        data = ctx.to_dict()
        
        assert data["request_id"] == "req123"
        assert data["trace_id"] == "trace456"
        assert data["metadata"]["key"] == "value"
    
    def test_from_dict(self):
        """Test context deserialization."""
        data = {
            "request_id": "req123",
            "trace_id": "trace456",
            "span_id": "span789",
            "metadata": {"key": "value"}
        }
        ctx = RequestContext.from_dict(data)
        
        assert ctx.request_id == "req123"
        assert ctx.metadata["key"] == "value"
    
    def test_elapsed_ms(self):
        """Test elapsed time calculation."""
        import time
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        time.sleep(0.01)  # 10ms
        elapsed = ctx.elapsed_ms()
        assert elapsed >= 10  # Should be at least 10ms


class TestContextManager:
    """Test ContextManager."""
    
    def setup_method(self):
        """Clear context before each test."""
        ContextManager.clear()
    
    def test_get_current_none(self):
        """Test getting current context when none set."""
        assert ContextManager.get_current() is None
    
    def test_set_and_get_current(self):
        """Test setting and getting current context."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        token = ContextManager.set_current(ctx)
        
        assert ContextManager.get_current() == ctx
        
        ContextManager.reset_current(token)
    
    def test_reset_current(self):
        """Test resetting current context."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        token = ContextManager.set_current(ctx)
        ContextManager.reset_current(token)
        
        assert ContextManager.get_current() is None
    
    def test_create_root(self):
        """Test creating root context."""
        ctx = ContextManager.create_root()
        
        assert ctx.request_id is not None
        assert ctx.trace_id is not None
        assert ctx.span_id is not None
        assert ContextManager.get_current() == ctx
    
    def test_child_context(self):
        """Test creating child context."""
        parent = ContextManager.create_root()
        child = ContextManager.child_context("child_span")
        
        assert child.request_id == parent.request_id
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id


class TestContextScope:
    """Test ContextScope context manager."""
    
    def setup_method(self):
        """Clear context before each test."""
        ContextManager.clear()
    
    def test_context_scope(self):
        """Test basic context scope."""
        with ContextScope() as ctx:
            assert ctx is not None
            assert ContextManager.get_current() == ctx
        
        assert ContextManager.get_current() is None
    
    def test_context_scope_with_metadata(self):
        """Test context scope with metadata."""
        with ContextScope(metadata={"service": "test"}) as ctx:
            assert ctx.metadata["service"] == "test"
    
    def test_nested_context_scope(self):
        """Test nested context scopes."""
        with ContextScope() as parent:
            with ContextScope(span_name="child") as child:
                assert child.parent_span_id == parent.span_id
                assert child.request_id == parent.request_id
    
    def test_context_scope_with_provided_context(self):
        """Test context scope with provided context."""
        provided = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        
        with ContextScope(context=provided) as ctx:
            assert ctx == provided


class TestAsyncContextScope:
    """Test AsyncContextScope async context manager."""
    
    def setup_method(self):
        """Clear context before each test."""
        ContextManager.clear()
    
    @pytest.mark.asyncio
    async def test_async_context_scope(self):
        """Test async context scope."""
        async with AsyncContextScope() as ctx:
            assert ctx is not None
            assert ContextManager.get_current() == ctx
        
        assert ContextManager.get_current() is None
    
    @pytest.mark.asyncio
    async def test_async_nested_scope(self):
        """Test nested async context scopes."""
        async with AsyncContextScope() as parent:
            async with AsyncContextScope(span_name="child") as child:
                assert child.parent_span_id == parent.span_id


class TestW3CTraceContext:
    """Test W3C Trace Context propagation."""
    
    def test_inject(self):
        """Test W3C header injection."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7"
        )
        
        headers = W3CTraceContext.inject(ctx)
        
        assert "traceparent" in headers
        assert headers["traceparent"].startswith("00-")
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in headers["traceparent"]
    
    def test_extract(self):
        """Test W3C header extraction."""
        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }
        
        ctx = W3CTraceContext.extract(headers)
        
        assert ctx is not None
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert ctx.parent_span_id == "00f067aa0ba902b7"
    
    def test_extract_invalid(self):
        """Test extraction of invalid traceparent."""
        headers = {"traceparent": "invalid"}
        
        ctx = W3CTraceContext.extract(headers)
        
        assert ctx is None


class TestB3Propagator:
    """Test B3 propagation format."""
    
    def test_inject_multi_header(self):
        """Test B3 multi-header injection."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7"
        )
        
        headers = B3Propagator.inject(ctx)
        
        assert "X-B3-TraceId" in headers
        assert "X-B3-SpanId" in headers
        assert headers["X-B3-Sampled"] == "1"
    
    def test_inject_single_header(self):
        """Test B3 single header injection."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7"
        )
        
        headers = B3Propagator.inject(ctx, single_header=True)
        
        assert "b3" in headers
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in headers["b3"]
    
    def test_extract_multi_header(self):
        """Test B3 multi-header extraction."""
        headers = {
            "X-B3-TraceId": "4bf92f3577b34da6a3ce929d0e0e4736",
            "X-B3-SpanId": "00f067aa0ba902b7",
            "X-B3-Sampled": "1"
        }
        
        ctx = B3Propagator.extract(headers)
        
        assert ctx is not None
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


class TestJaegerPropagator:
    """Test Jaeger propagation format."""
    
    def test_inject(self):
        """Test Jaeger header injection."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            metadata={"user_id": "123"}
        )
        
        headers = JaegerPropagator.inject(ctx)
        
        assert "uber-trace-id" in headers
        assert "uberctx-user_id" in headers
    
    def test_extract(self):
        """Test Jaeger header extraction."""
        headers = {
            "uber-trace-id": "4bf92f3577b34da6a3ce929d0e0e4736:00f067aa0ba902b7:0:1",
            "uberctx-user_id": "123"
        }
        
        ctx = JaegerPropagator.extract(headers)
        
        assert ctx is not None
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert ctx.metadata.get("user_id") == "123"


class TestContextPropagator:
    """Test unified context propagator."""
    
    def test_extract_w3c_priority(self):
        """Test W3C format has priority for extraction."""
        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-B3-TraceId": "different_trace_id"
        }
        
        propagator = ContextPropagator()
        ctx = propagator.extract(headers)
        
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    
    def test_inject_default(self):
        """Test default injection format."""
        ctx = RequestContext(
            request_id="req123",
            trace_id="trace456",
            span_id="span789"
        )
        
        propagator = ContextPropagator()
        headers = propagator.inject(ctx)
        
        assert "traceparent" in headers
        assert "X-Request-ID" in headers
        assert headers["X-Request-ID"] == "req123"
    
    def test_extract_or_create_existing(self):
        """Test extract_or_create with existing context."""
        headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }
        
        propagator = ContextPropagator()
        ctx = propagator.extract_or_create(headers)
        
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    
    def test_extract_or_create_new(self):
        """Test extract_or_create creates new when no context."""
        headers = {}
        
        propagator = ContextPropagator()
        ctx = propagator.extract_or_create(headers)
        
        assert ctx.request_id is not None
        assert ctx.trace_id is not None


class TestLogger:
    """Test contextual logging."""
    
    def setup_method(self):
        """Clear context before each test."""
        ContextManager.clear()
    
    def test_logger_creation(self):
        """Test logger creation."""
        logger = get_logger("test")
        assert logger is not None
    
    def test_logger_with_context(self, caplog):
        """Test logging with context."""
        import logging
        
        # Setup test logging
        caplog.set_level(logging.INFO)
        
        # Create context and log
        ctx = ContextManager.create_root()
        
        # Create custom handler to capture log records
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        
        test_logger = get_logger("test_context", level=logging.INFO, json_format=False)
        
        with patch.object(test_logger.logger, "info") as mock_info:
            test_logger.info("Test message")
            
            # Verify log was called
            assert mock_info.called
            
            # Check that context was passed in extra
            call_args = mock_info.call_args
            assert "extra" in call_args.kwargs
            assert call_args.kwargs["extra"]["request_id"] == ctx.request_id


class TestIntegration:
    """Integration tests."""
    
    def setup_method(self):
        """Clear context before each test."""
        ContextManager.clear()
    
    def test_full_flow(self):
        """Test complete flow from request to response."""
        # Simulate incoming request
        incoming_headers = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-Request-ID": "incoming-req-123"
        }
        
        # Extract context
        ctx = extract_context(incoming_headers)
        ContextManager.set_current(ctx)
        
        # Do some work
        child_ctx = ctx.child("database_query")
        
        # Prepare outgoing request
        outgoing_headers = inject_context(child_ctx)
        
        # Verify headers
        assert "traceparent" in outgoing_headers
        assert outgoing_headers["X-Request-ID"] == "incoming-req-123"
        
        # Trace ID should be preserved
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in outgoing_headers["traceparent"]
    
    @pytest.mark.asyncio
    async def test_async_flow(self):
        """Test async context propagation."""
        async def process_request():
            async with AsyncContextScope(metadata={"service": "api"}) as ctx:
                # Simulate some async work
                await asyncio.sleep(0.001)
                
                child_ctx = ctx.child("database_call")
                token = ContextManager.set_current(child_ctx)
                
                await asyncio.sleep(0.001)
                
                ContextManager.reset_current(token)
                return child_ctx
        
        result = await process_request()
        
        assert result is not None
        assert result.metadata.get("service") == "api"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
