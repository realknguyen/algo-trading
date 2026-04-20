"""Integration utilities for adapting existing code to use rate limiting.

This module provides helpers for integrating the rate limiting system
with existing exchange adapters.

Example:
    >>> from src.rate_limiter.integration import adapt_base_adapter
    >>>
    >>> # Adapt an existing adapter class
    >>> RateLimitedBinanceAdapter = adapt_base_adapter(BinanceAdapter)
    >>>
    >>> # Use the adapted class
    >>> async with RateLimitedBinanceAdapter(api_key, secret) as adapter:
    ...     account = await adapter.get_account()
"""

import functools
from typing import Type, Optional, Callable, Any, Dict
import asyncio

from src.rate_limiter import (
    GlobalRateCoordinator,
    ExchangeLimitConfig,
    Priority,
    RateLimitHeaders,
)


def rate_limited(
    path: str, method: str = "GET", weight: float = 1.0, priority: Priority = Priority.NORMAL
):
    """Decorator to mark a method as rate-limited.

    This decorator should be used on adapter methods that make HTTP requests.
    It automatically acquires rate limits before execution.

    Args:
        path: API endpoint path
        method: HTTP method
        weight: Request weight
        priority: Default priority

    Example:
        >>> class MyAdapter:
        ...     @rate_limited("/api/v3/account", weight=20)
        ...     async def get_account(self):
        ...         # This is rate limited
        ...         return await self._client.get("/api/v3/account")
    """

    def decorator(func):
        func._rate_limit_config = {
            "path": path,
            "method": method,
            "weight": weight,
            "priority": priority,
        }

        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            # Get coordinator from instance
            coordinator = getattr(self, "_rate_limit_coordinator", None)
            exchange = getattr(self, "exchange_name", "unknown")

            if coordinator:
                async with coordinator.request(
                    exchange, path, method, weight=weight, priority=kwargs.get("priority", priority)
                ):
                    return await func(self, *args, **kwargs)
            else:
                # No coordinator, just call the function
                return await func(self, *args, **kwargs)

        return wrapper

    return decorator


def adapt_base_adapter(
    adapter_class: Type,
    exchange_name: Optional[str] = None,
    config: Optional[ExchangeLimitConfig] = None,
    coordinator: Optional[GlobalRateCoordinator] = None,
) -> Type:
    """Adapt an existing adapter class to use rate limiting.

    Creates a new class that wraps the original adapter with rate limiting.

    Args:
        adapter_class: Original adapter class to wrap
        exchange_name: Exchange identifier (defaults to adapter.exchange_name)
        config: Rate limit configuration
        coordinator: Rate limit coordinator (uses singleton if None)

    Returns:
        New adapter class with rate limiting

    Example:
        >>> from src.adapters.binance import BinanceAdapter
        >>>
        >>> RateLimitedBinance = adapt_base_adapter(
        ...     BinanceAdapter,
        ...     exchange_name="binance",
        ...     config=ExchangeLimitConfig(
        ...         exchange_name="binance",
        ...         global_rate=20.0,
        ...         weight_limit=1200,
        ...         weight_window=60.0
        ...     )
        ... )
    """
    exchange = exchange_name or getattr(adapter_class, "exchange_name", "unknown")
    coord = coordinator or GlobalRateCoordinator.get_instance()

    # Register exchange if config provided
    if config:
        coord.register_exchange(exchange, config)

    class RateLimitedAdapter(adapter_class):
        """Rate-limited wrapper for adapter."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._rate_limit_coordinator = coord
            self._exchange = exchange

        async def _make_request(self, method: str, endpoint: str, **kwargs):
            """Override to add rate limiting."""
            # Determine weight from endpoint
            weight = kwargs.pop("weight", 1.0)
            priority = kwargs.pop("priority", Priority.NORMAL)

            # Acquire rate limit
            async with self._rate_limit_coordinator.request(
                self._exchange, endpoint, method, weight=weight, priority=priority
            ):
                # Make the request
                response = await super()._make_request(method, endpoint, **kwargs)

                # Record response if available
                if hasattr(response, "headers"):
                    self._rate_limit_coordinator.record_response(
                        self._exchange,
                        dict(response.headers),
                        getattr(response, "status_code", 200),
                        self._exchange,
                    )

                return response

    RateLimitedAdapter.__name__ = f"RateLimited{adapter_class.__name__}"
    RateLimitedAdapter.__doc__ = f"""
    Rate-limited wrapper for {adapter_class.__name__}.

    This class extends {adapter_class.__name__} with automatic rate limiting
    for all API requests.
    """

    return RateLimitedAdapter


def wrap_method_with_rate_limit(
    instance: Any,
    method_name: str,
    path: str,
    method: str = "GET",
    weight: float = 1.0,
    coordinator: Optional[GlobalRateCoordinator] = None,
    exchange: Optional[str] = None,
) -> None:
    """Wrap an existing instance method with rate limiting.

    Modifies the instance in-place to add rate limiting to a method.

    Args:
        instance: Adapter instance to modify
        method_name: Name of method to wrap
        path: API endpoint path
        method: HTTP method
        weight: Request weight
        coordinator: Rate limit coordinator
        exchange: Exchange name

    Example:
        >>> adapter = BinanceAdapter(api_key, secret)
        >>> wrap_method_with_rate_limit(
        ...     adapter,
        ...     "get_account",
        ...     "/api/v3/account",
        ...     weight=20
        ... )
    """
    original_method = getattr(instance, method_name)
    coord = coordinator or GlobalRateCoordinator.get_instance()
    exc = exchange or getattr(instance, "exchange_name", "unknown")

    @functools.wraps(original_method)
    async def wrapped(*args, **kwargs):
        async with coord.request(exc, path, method, weight=weight):
            return await original_method(*args, **kwargs)

    setattr(instance, method_name, wrapped)


class RateLimitMixin:
    """Mixin class to add rate limiting to existing adapters.

    Use this as a base class or mixin to add rate limiting capabilities.

    Example:
        >>> class MyAdapter(RateLimitMixin, BaseAdapter):
        ...     exchange_name = "myexchange"
        ...
        ...     def __init__(self, *args, **kwargs):
        ...         super().__init__(*args, **kwargs)
        ...         self._init_rate_limiting(
        ...             ExchangeLimitConfig(exchange_name="myexchange", global_rate=10.0)
        ...         )
        ...
        ...     async def get_account(self):
        ...         return await self._rate_limited_request(
        ...             "/api/account", "GET", weight=1,
        ...             lambda: self._client.get("/api/account")
        ...         )
    """

    def _init_rate_limiting(
        self, config: ExchangeLimitConfig, coordinator: Optional[GlobalRateCoordinator] = None
    ) -> None:
        """Initialize rate limiting for this adapter.

        Args:
            config: Rate limit configuration
            coordinator: Rate limit coordinator (uses singleton if None)
        """
        self._rate_limit_coordinator = coordinator or GlobalRateCoordinator.get_instance()
        self._rate_limit_config = config

        # Register with coordinator
        self._rate_limit_coordinator.register_exchange(config.exchange_name, config)

    async def _rate_limited_request(
        self,
        path: str,
        method: str,
        weight: float,
        request_func: Callable,
        priority: Priority = Priority.NORMAL,
        **kwargs,
    ) -> Any:
        """Execute a request with rate limiting.

        Args:
            path: API endpoint path
            method: HTTP method
            weight: Request weight
            request_func: Async function to execute
            priority: Request priority
            **kwargs: Additional arguments

        Returns:
            Result of request_func
        """
        exchange = getattr(self, "exchange_name", "unknown")

        async with self._rate_limit_coordinator.request(
            exchange, path, method, weight=weight, priority=priority
        ):
            response = await request_func()

            # Record response if it has headers
            if hasattr(response, "headers"):
                self._rate_limit_coordinator.record_response(
                    exchange,
                    dict(response.headers),
                    getattr(response, "status_code", 200),
                    exchange,
                )

            return response

    def record_rate_limit_headers(self, headers: Dict[str, str], status_code: int = 200) -> None:
        """Manually record rate limit headers.

        Use this when the response is processed outside the standard flow.

        Args:
            headers: Response headers
            status_code: HTTP status code
        """
        exchange = getattr(self, "exchange_name", "unknown")
        self._rate_limit_coordinator.record_response(exchange, headers, status_code, exchange)


def create_middleware(
    coordinator: Optional[GlobalRateCoordinator] = None,
    get_exchange: Optional[Callable[[Any], str]] = None,
    get_path: Optional[Callable[[Any], str]] = None,
) -> Callable:
    """Create ASGI/Starlette-style middleware for rate limiting.

    This can be used with FastAPI or other ASGI frameworks.

    Args:
        coordinator: Rate limit coordinator
        get_exchange: Function to extract exchange from request
        get_path: Function to extract path from request

    Returns:
        Middleware function

    Example:
        >>> from fastapi import FastAPI
        >>>
        >>> app = FastAPI()
        >>> middleware = create_middleware()
        >>> app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    """
    coord = coordinator or GlobalRateCoordinator.get_instance()

    async def middleware(request, call_next):
        """Process request with rate limiting."""
        exchange = get_exchange(request) if get_exchange else "default"
        path = get_path(request) if get_path else str(request.url.path)

        async with coord.request(exchange, path, request.method):
            response = await call_next(request)

            # Record response headers
            coord.record_response(exchange, dict(response.headers), response.status_code, exchange)

            return response

    return middleware
