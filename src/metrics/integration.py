"""Metrics integration module for HTTP clients and exchange adapters.

This module provides decorators and middleware for automatic metrics collection
from exchange adapters and HTTP clients.

Usage:
    from src.metrics.integration import instrument_adapter, instrument_http_client

    # Instrument an adapter class
    @instrument_adapter
    class MyExchangeAdapter(BaseExchangeAdapter):
        ...

    # Instrument an HTTP client
    async with instrument_http_client(httpx.AsyncClient()) as client:
        ...
"""

import asyncio
import functools
import time
from typing import Callable, Any, Optional, Type, Dict
from contextlib import asynccontextmanager

import httpx
import structlog

from src.metrics.collector import MetricsCollector, get_collector


logger = structlog.get_logger(__name__)


def instrument_request(
    exchange: str, method: str, endpoint: str, collector: Optional[MetricsCollector] = None
):
    """Decorator to instrument an async method with request metrics.

    Args:
        exchange: Exchange name
        method: HTTP method
        endpoint: API endpoint
        collector: Metrics collector (uses global if not provided)

    Usage:
        @instrument_request("binance", "GET", "/api/v3/account")
        async def get_account(self):
            ...
    """

    def decorator(func: Callable) -> Callable:
        coll = collector or get_collector()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = 200
            error_type = None

            await coll.record_request_started(exchange, method)

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status_code = getattr(e, "status_code", 500)
                error_type = type(e).__name__
                raise
            finally:
                duration = time.time() - start_time
                await coll.record_request_completed(
                    exchange, method, endpoint, duration, status_code, error_type
                )

        return wrapper

    return decorator


def instrument_order_placement(
    exchange: str,
    symbol_attr: str = "symbol",
    order_type_attr: str = "order_type",
    collector: Optional[MetricsCollector] = None,
):
    """Decorator to instrument order placement methods.

    Args:
        exchange: Exchange name
        symbol_attr: Attribute name for symbol in order object
        order_type_attr: Attribute name for order type in order object
        collector: Metrics collector (uses global if not provided)

    Usage:
        @instrument_order_placement("binance")
        async def place_order(self, order: Order):
            ...
    """

    def decorator(func: Callable) -> Callable:
        coll = collector or get_collector()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Get order from args or kwargs
            order = args[1] if len(args) > 1 else kwargs.get("order")

            symbol = getattr(order, symbol_attr, "unknown")
            order_type = getattr(order, order_type_attr, "unknown")

            async with coll.track_order_placement(exchange, symbol, order_type):
                result = await func(*args, **kwargs)

                # Record fill if order is filled
                if hasattr(result, "status") and "filled" in str(result.status).lower():
                    await coll.record_order_filled(exchange, symbol)
                    await coll.update_fill_rate(exchange, symbol)

                return result

        return wrapper

    return decorator


def instrument_connection(
    exchange: str, connection_type: str = "rest", collector: Optional[MetricsCollector] = None
):
    """Decorator to instrument connection methods.

    Args:
        exchange: Exchange name
        connection_type: Type of connection (rest, websocket)
        collector: Metrics collector (uses global if not provided)
    """

    def decorator(func: Callable) -> Callable:
        coll = collector or get_collector()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                result = await func(*args, **kwargs)

                # Check if connection was successful
                if isinstance(result, bool) and result:
                    await coll.record_connection_opened(exchange, connection_type)
                elif result is not None:
                    await coll.record_connection_opened(exchange, connection_type)

                return result
            except Exception as e:
                await coll.record_connection_failed(exchange, type(e).__name__)
                raise

        return wrapper

    return decorator


def instrument_adapter_methods(
    cls: Type, exchange_name: str, collector: Optional[MetricsCollector] = None
) -> Type:
    """Instrument all methods of an exchange adapter class.

    This is a class decorator that automatically instruments common adapter methods.

    Args:
        cls: Adapter class to instrument
        exchange_name: Name of the exchange
        collector: Metrics collector (uses global if not provided)

    Returns:
        Instrumented class
    """
    coll = collector or get_collector()

    # Define method mappings
    request_methods = {
        "get_account": ("GET", "/account"),
        "get_balances": ("GET", "/balances"),
        "get_ticker": ("GET", "/ticker"),
        "get_orderbook": ("GET", "/orderbook"),
        "get_historical_candles": ("GET", "/klines"),
        "get_open_orders": ("GET", "/orders"),
        "get_order_status": ("GET", "/order"),
        "get_positions": ("GET", "/positions"),
    }

    order_methods = ["place_order"]
    connection_methods = ["connect", "_connect"]

    # Instrument request methods
    for method_name, (http_method, endpoint) in request_methods.items():
        if hasattr(cls, method_name):
            original = getattr(cls, method_name)
            setattr(
                cls,
                method_name,
                instrument_request(exchange_name, http_method, endpoint, coll)(original),
            )

    # Instrument order methods
    for method_name in order_methods:
        if hasattr(cls, method_name):
            original = getattr(cls, method_name)
            setattr(
                cls,
                method_name,
                instrument_order_placement(exchange_name, collector=coll)(original),
            )

    # Instrument connection methods
    for method_name in connection_methods:
        if hasattr(cls, method_name):
            original = getattr(cls, method_name)
            setattr(cls, method_name, instrument_connection(exchange_name, "rest", coll)(original))

    return cls


class InstrumentedHTTPClient:
    """Wrapper for httpx.AsyncClient with automatic metrics collection.

    Usage:
        client = InstrumentedHTTPClient("binance")
        await client.get("/api/v3/ticker")
    """

    def __init__(
        self, exchange: str, collector: Optional[MetricsCollector] = None, **client_kwargs
    ):
        """Initialize instrumented HTTP client.

        Args:
            exchange: Exchange name
            collector: Metrics collector
            **client_kwargs: Arguments for httpx.AsyncClient
        """
        self.exchange = exchange
        self.collector = collector or get_collector()
        self.client = httpx.AsyncClient(**client_kwargs)

    async def _make_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make request with metrics tracking."""
        # Extract endpoint from URL
        endpoint = url.split("?")[0]  # Remove query params

        async with self.collector.track_request(self.exchange, method, endpoint):
            response = await self.client.request(method, url, **kwargs)
            return response

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Make GET request."""
        return await self._make_request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Make POST request."""
        return await self._make_request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """Make PUT request."""
        return await self._make_request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """Make DELETE request."""
        return await self._make_request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """Make PATCH request."""
        return await self._make_request("PATCH", url, **kwargs)

    async def aclose(self) -> None:
        """Close the client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()


class MetricsMiddleware:
    """Middleware for tracking HTTP request/response metrics.

    Can be used with httpx event hooks or custom HTTP clients.
    """

    def __init__(self, exchange: str, collector: Optional[MetricsCollector] = None):
        self.exchange = exchange
        self.collector = collector or get_collector()
        self._request_times: Dict[str, float] = {}

    def on_request(self, request: httpx.Request) -> None:
        """Called when request is about to be sent."""
        request_id = str(id(request))
        self._request_times[request_id] = time.time()

        # Start tracking
        asyncio.create_task(self.collector.record_request_started(self.exchange, request.method))

    def on_response(self, response: httpx.Response) -> None:
        """Called when response is received."""
        request_id = str(id(response.request))
        start_time = self._request_times.pop(request_id, None)

        if start_time:
            duration = time.time() - start_time
            endpoint = response.request.url.path

            asyncio.create_task(
                self.collector.record_request_completed(
                    self.exchange,
                    response.request.method,
                    endpoint,
                    duration,
                    response.status_code,
                    None,
                )
            )

    def on_error(self, request: httpx.Request, error: Exception) -> None:
        """Called when request errors."""
        request_id = str(id(request))
        start_time = self._request_times.pop(request_id, None)

        if start_time:
            duration = time.time() - start_time
            endpoint = request.url.path
            error_type = type(error).__name__
            status_code = getattr(error, "status_code", 500)

            asyncio.create_task(
                self.collector.record_request_completed(
                    self.exchange, request.method, endpoint, duration, status_code, error_type
                )
            )


def patch_adapter_for_metrics(adapter_class: Type, exchange_name: Optional[str] = None) -> Type:
    """Patch an adapter class for automatic metrics collection.

    This modifies the class in-place to add metrics collection to key methods.

    Args:
        adapter_class: Adapter class to patch
        exchange_name: Exchange name (uses class.exchange_name if not provided)

    Returns:
        Patched class
    """
    exchange = exchange_name or getattr(adapter_class, "exchange_name", "unknown")
    collector = get_collector()

    # Store original methods
    original_make_request = getattr(adapter_class, "_make_request", None)
    original_place_order = getattr(adapter_class, "place_order", None)
    original_connect = getattr(adapter_class, "connect", None)

    async def instrumented_make_request(
        self, method: str, endpoint: str, **kwargs
    ) -> Dict[str, Any]:
        """Instrumented make_request method."""
        start_time = time.time()
        status_code = 200
        error_type = None

        await collector.record_request_started(exchange, method)

        try:
            if original_make_request:
                result = await original_make_request(self, method, endpoint, **kwargs)
            else:
                # Call parent class method
                from src.adapters.base_adapter import BaseExchangeAdapter

                result = await BaseExchangeAdapter._make_request(self, method, endpoint, **kwargs)
            return result
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            error_type = type(e).__name__
            raise
        finally:
            duration = time.time() - start_time
            await collector.record_request_completed(
                exchange, method, endpoint, duration, status_code, error_type
            )

    async def instrumented_place_order(self, order) -> Any:
        """Instrumented place_order method."""
        symbol = getattr(order, "symbol", "unknown")
        order_type = getattr(order, "order_type", "unknown")

        async with collector.track_order_placement(exchange, symbol, order_type):
            if original_place_order:
                result = await original_place_order(self, order)
            else:
                raise NotImplementedError("place_order not implemented")

            # Record fill metrics
            if hasattr(result, "status"):
                if "filled" in str(result.status).lower():
                    await collector.record_order_filled(exchange, symbol)
                await collector.update_fill_rate(exchange, symbol)

            return result

    async def instrumented_connect(self) -> bool:
        """Instrumented connect method."""
        try:
            if original_connect:
                result = await original_connect(self)
            else:
                from src.adapters.base_adapter import BaseExchangeAdapter

                result = await BaseExchangeAdapter.connect(self)

            if result:
                await collector.record_connection_opened(exchange, "rest")
            return result
        except Exception as e:
            await collector.record_connection_failed(exchange, type(e).__name__)
            raise

    # Replace methods
    adapter_class._make_request = instrumented_make_request
    adapter_class.place_order = instrumented_place_order
    adapter_class.connect = instrumented_connect

    logger.info(f"Patched {adapter_class.__name__} for metrics collection")
    return adapter_class


# Example integration helper
async def setup_metrics_for_exchange(adapter, exchange_name: Optional[str] = None) -> None:
    """Set up metrics collection for an exchange adapter instance.

    Args:
        adapter: Exchange adapter instance
        exchange_name: Exchange name (uses adapter.exchange_name if not provided)
    """
    exchange = exchange_name or getattr(adapter, "exchange_name", "unknown")
    collector = get_collector()

    # Register with dashboard if available
    from src.metrics.dashboard import get_dashboard

    dashboard = get_dashboard()
    if dashboard:
        dashboard.register_exchange(exchange)

    logger.info(f"Metrics set up for {exchange}")


__all__ = [
    "instrument_request",
    "instrument_order_placement",
    "instrument_connection",
    "instrument_adapter_methods",
    "InstrumentedHTTPClient",
    "MetricsMiddleware",
    "patch_adapter_for_metrics",
    "setup_metrics_for_exchange",
]
