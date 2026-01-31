"""
Request Context Management for Distributed Tracing.

Provides async-safe context propagation using ContextVars for tracking
requests across async boundaries.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class RequestContext:
    """
    Immutable request context for distributed tracing.
    
    Tracks request_id, trace_id, span_id and their relationships
    for end-to-end request correlation across services.
    """
    request_id: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def child(self, span_name: Optional[str] = None) -> RequestContext:
        """Create a child context for sub-operations."""
        from .generator import RequestIDGenerator
        
        return RequestContext(
            request_id=self.request_id,
            trace_id=self.trace_id,
            span_id=RequestIDGenerator.generate_span_id(),
            parent_span_id=self.span_id,
            start_time=time.time(),
            metadata={**self.metadata, "span_name": span_name} if span_name else self.metadata.copy()
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to dictionary."""
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RequestContext:
        """Deserialize context from dictionary."""
        return cls(
            request_id=data["request_id"],
            trace_id=data["trace_id"],
            span_id=data["span_id"],
            parent_span_id=data.get("parent_span_id"),
            start_time=data.get("start_time", time.time()),
            metadata=data.get("metadata", {}),
        )
    
    def elapsed_ms(self) -> float:
        """Get elapsed time since context creation in milliseconds."""
        return (time.time() - self.start_time) * 1000


# ContextVars for async-safe propagation
_request_context: ContextVar[Optional[RequestContext]] = ContextVar(
    "request_context", default=None
)
_context_stack: ContextVar[List[RequestContext]] = ContextVar(
    "context_stack", default_factory=list
)


class ContextManager:
    """
    Manages request context lifecycle across async boundaries.
    
    Uses ContextVars for thread-safe and async-safe context propagation.
    """
    
    @staticmethod
    def get_current() -> Optional[RequestContext]:
        """Get the current request context."""
        return _request_context.get()
    
    @staticmethod
    def set_current(context: RequestContext) -> Token:
        """Set the current request context. Returns token for reset."""
        token = _request_context.set(context)
        # Also push to stack for nested contexts
        stack = _context_stack.get()
        stack.append(context)
        _context_stack.set(stack)
        return token
    
    @staticmethod
    def reset_current(token: Token) -> None:
        """Reset context using token from set_current."""
        _request_context.reset(token)
        # Pop from stack
        stack = _context_stack.get()
        if stack:
            stack.pop()
            _context_stack.set(stack)
    
    @staticmethod
    def clear() -> None:
        """Clear current context."""
        _request_context.set(None)
        _context_stack.set([])
    
    @staticmethod
    def get_stack() -> List[RequestContext]:
        """Get the context stack for nested operation tracking."""
        return _context_stack.get().copy()
    
    @classmethod
    def create_root(
        cls,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RequestContext:
        """Create a new root context."""
        from .generator import RequestIDGenerator
        
        context = RequestContext(
            request_id=request_id or RequestIDGenerator.generate_request_id(),
            trace_id=trace_id or RequestIDGenerator.generate_trace_id(),
            span_id=RequestIDGenerator.generate_span_id(),
            metadata=metadata or {}
        )
        cls.set_current(context)
        return context
    
    @classmethod
    def child_context(cls, span_name: Optional[str] = None) -> RequestContext:
        """Create a child context from current, or root if none exists."""
        current = cls.get_current()
        if current:
            child = current.child(span_name)
        else:
            child = cls.create_root(metadata={"span_name": span_name} if span_name else {})
        cls.set_current(child)
        return child


class ContextScope:
    """
    Context manager for scoped context operations.
    
    Usage:
        with ContextScope() as ctx:
            # Do work with context
            pass
    """
    
    def __init__(
        self,
        context: Optional[RequestContext] = None,
        span_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self._provided = context
        self._span_name = span_name
        self._metadata = metadata
        self._token: Optional[Token] = None
        self.context: Optional[RequestContext] = None
    
    def __enter__(self) -> RequestContext:
        if self._provided:
            self.context = self._provided
        elif ContextManager.get_current():
            self.context = ContextManager.get_current().child(self._span_name)
            if self._metadata:
                # Create new context with merged metadata
                self.context = RequestContext(
                    request_id=self.context.request_id,
                    trace_id=self.context.trace_id,
                    span_id=self.context.span_id,
                    parent_span_id=self.context.parent_span_id,
                    start_time=self.context.start_time,
                    metadata={**self.context.metadata, **self._metadata}
                )
        else:
            self.context = ContextManager.create_root(metadata=self._metadata)
        
        self._token = ContextManager.set_current(self.context)
        return self.context
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token:
            ContextManager.reset_current(self._token)


class AsyncContextScope:
    """
    Async context manager for scoped context operations.
    
    Usage:
        async with AsyncContextScope() as ctx:
            # Do async work with context
            pass
    """
    
    def __init__(
        self,
        context: Optional[RequestContext] = None,
        span_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self._scope = ContextScope(context, span_name, metadata)
    
    async def __aenter__(self) -> RequestContext:
        return self._scope.__enter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._scope.__exit__(exc_type, exc_val, exc_tb)


def get_current_context() -> Optional[RequestContext]:
    """Convenience function to get current context."""
    return ContextManager.get_current()


def ensure_context(
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> RequestContext:
    """
    Ensure a context exists - return current or create new root.
    
    Usage:
        ctx = ensure_context()
        # ctx is either existing or newly created
    """
    current = ContextManager.get_current()
    if current:
        return current
    return ContextManager.create_root(request_id, trace_id, metadata)
