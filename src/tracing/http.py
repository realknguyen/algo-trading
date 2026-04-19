"""
HTTP Client Integration for Distributed Tracing.

Provides traced HTTP client that automatically:
- Generates/injects trace context into outgoing requests
- Extracts trace context from incoming responses
- Logs request/response with correlation IDs
- Tracks request timing
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union, AsyncGenerator

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from ..context import ContextManager, RequestContext, AsyncContextScope
from ..generator import RequestIDGenerator
from ..logger import ContextualLogger, get_logger
from ..propagation import ContextPropagator, default_propagator


@dataclass
class RequestMetrics:
    """Metrics for an HTTP request."""

    url: str
    method: str
    status_code: Optional[int] = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    request_size: Optional[int] = None
    response_size: Optional[int] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.end_time > 0 and self.start_time > 0:
            self.duration_ms = (self.end_time - self.start_time) * 1000


class TracedHTTPClient:
    """
    HTTP client with automatic distributed tracing.

    Features:
    - Auto-generates trace context for each request
    - Injects trace headers (W3C/B3/Jaeger)
    - Logs requests with correlation IDs
    - Tracks timing metrics

    Usage:
        async with TracedHTTPClient() as client:
            response = await client.get("https://api.example.com/data")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        propagator: Optional[ContextPropagator] = None,
        logger: Optional[ContextualLogger] = None,
        timeout: float = 30.0,
        backend: str = "auto",  # "auto", "aiohttp", "httpx"
    ):
        """
        Initialize traced HTTP client.

        Args:
            base_url: Base URL for all requests
            headers: Default headers for all requests
            propagator: Context propagator (uses default if None)
            logger: Logger instance (creates default if None)
            timeout: Request timeout in seconds
            backend: HTTP backend to use ("auto", "aiohttp", "httpx")
        """
        self.base_url = base_url
        self.default_headers = headers or {}
        self.propagator = propagator or default_propagator
        self.logger = logger or get_logger("tracing.http")
        self.timeout = timeout

        # Determine backend
        self._backend = self._select_backend(backend)
        self._client = None
        self._closed = True

    def _select_backend(self, backend: str) -> str:
        """Select HTTP backend."""
        if backend == "auto":
            if AIOHTTP_AVAILABLE:
                return "aiohttp"
            elif HTTPX_AVAILABLE:
                return "httpx"
            else:
                raise ImportError("No HTTP backend available. Install aiohttp or httpx.")
        elif backend == "aiohttp":
            if not AIOHTTP_AVAILABLE:
                raise ImportError("aiohttp not installed")
            return "aiohttp"
        elif backend == "httpx":
            if not HTTPX_AVAILABLE:
                raise ImportError("httpx not installed")
            return "httpx"
        else:
            raise ValueError(f"Unknown backend: {backend}")

    async def __aenter__(self) -> TracedHTTPClient:
        await self._open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._close()

    async def _open(self) -> None:
        """Open HTTP client session."""
        if self._backend == "aiohttp":
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._client = aiohttp.ClientSession(timeout=timeout)
        elif self._backend == "httpx":
            timeout = httpx.Timeout(self.timeout)
            self._client = httpx.AsyncClient(timeout=timeout, base_url=self.base_url or "")

        self._closed = False

    async def _close(self) -> None:
        """Close HTTP client session."""
        if self._client and not self._closed:
            await self._client.close()
            self._closed = True

    def _build_url(self, path: str) -> str:
        """Build full URL from path."""
        if self.base_url and not path.startswith(("http://", "https://")):
            return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        return path

    def _prepare_headers(
        self, context: RequestContext, extra_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Prepare headers with trace context."""
        # Inject trace context
        headers = self.propagator.inject(context, {})

        # Add default headers
        headers.update(self.default_headers)

        # Add extra headers
        if extra_headers:
            headers.update(extra_headers)

        return headers

    async def _execute_request(
        self, method: str, url: str, headers: Dict[str, str], **kwargs
    ) -> Any:
        """Execute HTTP request with selected backend."""
        if self._backend == "aiohttp":
            async with self._client.request(method, url, headers=headers, **kwargs) as response:
                # Read response body
                body = await response.read()
                return {
                    "status": response.status,
                    "headers": dict(response.headers),
                    "body": body,
                }

        elif self._backend == "httpx":
            response = await self._client.request(method, url, headers=headers, **kwargs)
            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": response.content,
            }

    async def request(
        self,
        method: str,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Make traced HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL (or path if base_url set)
            context: Request context (creates new if None)
            headers: Additional headers
            **kwargs: Additional request arguments

        Returns:
            Response dict with status, headers, body
        """
        if self._closed:
            await self._open()

        # Build full URL
        full_url = self._build_url(url)

        # Create or use provided context
        if context is None:
            parent_context = ContextManager.get_current()
            if parent_context:
                context = parent_context.child(f"http_{method.lower()}")
            else:
                context = ContextManager.create_root(
                    metadata={"http_method": method, "http_url": full_url}
                )

        # Prepare headers
        request_headers = self._prepare_headers(context, headers)

        # Create metrics
        metrics = RequestMetrics(url=full_url, method=method, start_time=time.time())

        # Log request start
        self.logger.debug(
            f"HTTP {method} {url}",
            extra={
                "http_method": method,
                "http_url": full_url,
                "trace_id": context.trace_id,
                "span_id": context.span_id,
            },
        )

        try:
            # Set context for request
            token = ContextManager.set_current(context)

            try:
                # Execute request
                response = await self._execute_request(method, full_url, request_headers, **kwargs)

                metrics.end_time = time.time()
                metrics.status_code = response["status"]

                # Log success
                self.logger.info(
                    f"HTTP {method} {url} - {metrics.status_code} ({metrics.duration_ms:.2f}ms)",
                    extra={
                        "http_method": method,
                        "http_url": full_url,
                        "http_status": metrics.status_code,
                        "http_duration_ms": metrics.duration_ms,
                        "trace_id": context.trace_id,
                        "span_id": context.span_id,
                    },
                )

                return response

            finally:
                ContextManager.reset_current(token)

        except Exception as e:
            metrics.end_time = time.time()
            metrics.error = str(e)

            # Log error
            self.logger.exception(
                f"HTTP {method} {url} failed",
                extra={
                    "http_method": method,
                    "http_url": full_url,
                    "http_duration_ms": metrics.duration_ms,
                    "error": str(e),
                    "trace_id": context.trace_id,
                    "span_id": context.span_id,
                },
            )
            raise

    async def get(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make GET request."""
        return await self.request("GET", url, context, headers, **kwargs)

    async def post(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make POST request."""
        return await self.request("POST", url, context, headers, **kwargs)

    async def put(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make PUT request."""
        return await self.request("PUT", url, context, headers, **kwargs)

    async def patch(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make PATCH request."""
        return await self.request("PATCH", url, context, headers, **kwargs)

    async def delete(
        self,
        url: str,
        context: Optional[RequestContext] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make DELETE request."""
        return await self.request("DELETE", url, context, headers, **kwargs)


class TracedResponse:
    """
    Wrapper for HTTP responses with trace context.

    Extracts trace context from response headers for
    end-to-end request correlation.
    """

    def __init__(
        self,
        response: Any,
        metrics: RequestMetrics,
        context: RequestContext,
        propagator: Optional[ContextPropagator] = None,
    ):
        self._response = response
        self.metrics = metrics
        self.context = context
        self.propagator = propagator or default_propagator

        # Extract context from response headers if present
        self.response_context = self._extract_response_context()

    def _extract_response_context(self) -> Optional[RequestContext]:
        """Extract trace context from response headers."""
        if hasattr(self._response, "headers"):
            return self.propagator.extract(dict(self._response.headers))
        return None

    @property
    def status(self) -> int:
        """Get response status code."""
        if hasattr(self._response, "status"):
            return self._response.status
        elif hasattr(self._response, "status_code"):
            return self._response.status_code
        return 0

    @property
    def headers(self) -> Dict[str, str]:
        """Get response headers."""
        if hasattr(self._response, "headers"):
            return dict(self._response.headers)
        return {}

    async def text(self) -> str:
        """Get response body as text."""
        if hasattr(self._response, "text"):
            if asyncio.iscoroutinefunction(self._response.text):
                return await self._response.text()
            return self._response.text()

        body = await self.read()
        return body.decode("utf-8")

    async def json(self) -> Any:
        """Get response body as JSON."""
        import json

        text = await self.text()
        return json.loads(text)

    async def read(self) -> bytes:
        """Read response body."""
        if hasattr(self._response, "read"):
            if asyncio.iscoroutinefunction(self._response.read):
                return await self._response.read()
            return self._response.read()

        if hasattr(self._response, "content"):
            return self._response.content

        return b""


# Middleware-style integration
async def traced_http_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    context: Optional[RequestContext] = None,
    logger: Optional[ContextualLogger] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Standalone traced HTTP request function.

    Usage:
        response = await traced_http_request("GET", "https://api.example.com/data")
    """
    async with TracedHTTPClient(logger=logger) as client:
        return await client.request(method, url, context, headers, **kwargs)


# Convenience functions
async def traced_get(url: str, **kwargs) -> Dict[str, Any]:
    """Make traced GET request."""
    return await traced_http_request("GET", url, **kwargs)


async def traced_post(url: str, **kwargs) -> Dict[str, Any]:
    """Make traced POST request."""
    return await traced_http_request("POST", url, **kwargs)


async def traced_put(url: str, **kwargs) -> Dict[str, Any]:
    """Make traced PUT request."""
    return await traced_http_request("PUT", url, **kwargs)


async def traced_patch(url: str, **kwargs) -> Dict[str, Any]:
    """Make traced PATCH request."""
    return await traced_http_request("PATCH", url, **kwargs)


async def traced_delete(url: str, **kwargs) -> Dict[str, Any]:
    """Make traced DELETE request."""
    return await traced_http_request("DELETE", url, **kwargs)
