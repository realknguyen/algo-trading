"""Rate-limited HTTP client for exchange adapters.

Integrates the rate limiting system with HTTPX for automatic
rate limiting on all exchange requests.

Example:
    >>> from src.rate_limiter.http_client import RateLimitedClient
    >>>
    >>> client = RateLimitedClient(
    ...     exchange="binance",
    ...     coordinator=coordinator
    ... )
    >>>
    >>> async with client:
    ...     response = await client.get("/api/v3/account")
"""

import asyncio
from typing import Dict, Any, Optional, Union, Callable
from urllib.parse import urlparse

import httpx

from src.rate_limiter.coordinator import GlobalRateCoordinator, Priority, RateLimitHeaders
from src.rate_limiter.rate_limiter import ExchangeLimitConfig


class RateLimitedClient:
    """HTTPX client with integrated rate limiting.

    Wraps httpx.AsyncClient and automatically applies rate limits
    to all requests based on the configured coordinator.

    Example:
        >>> client = RateLimitedClient(
        ...     exchange="binance",
        ...     base_url="https://api.binance.com",
        ...     coordinator=coordinator
        ... )
        >>>
        >>> async with client:
        ...     # This request is automatically rate limited
        ...     response = await client.get("/api/v3/account")
    """

    def __init__(
        self,
        exchange: str,
        coordinator: Optional[GlobalRateCoordinator] = None,
        base_url: str = "",
        exchange_type: str = "generic",
        default_priority: Priority = Priority.NORMAL,
        auto_parse_headers: bool = True,
        **client_kwargs,
    ):
        """Initialize rate-limited HTTP client.

        Args:
            exchange: Exchange identifier
            coordinator: Rate limit coordinator (uses singleton if None)
            base_url: Base URL for requests
            exchange_type: Type for header parsing (binance, coinbase, etc.)
            default_priority: Default request priority
            auto_parse_headers: Automatically parse rate limit headers
            **client_kwargs: Additional arguments for httpx.AsyncClient
        """
        self._exchange = exchange
        self._coordinator = coordinator or GlobalRateCoordinator.get_instance()
        self._exchange_type = exchange_type
        self._default_priority = default_priority
        self._auto_parse_headers = auto_parse_headers

        # Create underlying HTTPX client
        self._client = httpx.AsyncClient(base_url=base_url, **client_kwargs)

        # Request weight mapping (can be customized)
        self._weight_map: Dict[str, int] = {}

        # Callbacks
        self._on_rate_limit: Optional[Callable] = None
        self._on_response: Optional[Callable] = None

    def set_weight(self, path_pattern: str, weight: int) -> None:
        """Set request weight for a path pattern.

        Args:
            path_pattern: URL path or pattern
            weight: Request weight for rate limiting
        """
        self._weight_map[path_pattern] = weight

    def set_weights(self, weights: Dict[str, int]) -> None:
        """Set multiple request weights."""
        self._weight_map.update(weights)

    def _get_weight(self, path: str, method: str) -> int:
        """Get weight for a request path."""
        # Check exact matches first
        if path in self._weight_map:
            return self._weight_map[path]

        # Check pattern matches
        import fnmatch

        for pattern, weight in self._weight_map.items():
            if fnmatch.fnmatch(path, pattern):
                return weight

        # Default weights by method
        if method in ("POST", "PUT", "DELETE"):
            return 2  # Write operations typically cost more
        return 1  # Read operations

    def _get_path(self, url: Union[str, httpx.URL]) -> str:
        """Extract path from URL."""
        if isinstance(url, str):
            if url.startswith("http"):
                parsed = urlparse(url)
                return parsed.path
            return url
        return str(url.path)

    async def _make_request(
        self, method: str, url: Union[str, httpx.URL], **kwargs
    ) -> httpx.Response:
        """Make a rate-limited request."""
        path = self._get_path(url)
        weight = kwargs.pop("weight", None) or self._get_weight(path, method)
        priority = kwargs.pop("priority", self._default_priority)
        timeout = kwargs.pop("request_timeout", None)

        # Acquire rate limit
        acquired = await self._coordinator.acquire(
            self._exchange, path, method, weight=weight, priority=priority, timeout=timeout
        )

        if not acquired:
            raise RateLimitError(
                f"Could not acquire rate limit for {method} {url}", exchange=self._exchange
            )

        try:
            # Make the actual request
            response = await self._client.request(method, url, **kwargs)

            # Parse rate limit headers
            if self._auto_parse_headers:
                self._coordinator.record_response(
                    self._exchange,
                    dict(response.headers),
                    response.status_code,
                    self._exchange_type,
                )

            # Call response callback
            if self._on_response:
                self._on_response(response)

            return response

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Record rate limit hit
                self._coordinator.record_response(
                    self._exchange, dict(e.response.headers), 429, self._exchange_type
                )

                if self._on_rate_limit:
                    self._on_rate_limit(e)

            raise

        finally:
            # Always release the rate limit
            self._coordinator.release(self._exchange, path, method)

    # HTTP method wrappers

    async def get(
        self,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited GET request."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request("GET", url, **kwargs)

    async def post(
        self,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited POST request."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request("POST", url, **kwargs)

    async def put(
        self,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited PUT request."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request("PUT", url, **kwargs)

    async def delete(
        self,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited DELETE request."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request("DELETE", url, **kwargs)

    async def patch(
        self,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited PATCH request."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request("PATCH", url, **kwargs)

    async def request(
        self,
        method: str,
        url: Union[str, httpx.URL],
        *,
        weight: Optional[int] = None,
        priority: Optional[Priority] = None,
        **kwargs,
    ) -> httpx.Response:
        """Make a rate-limited request with custom method."""
        if weight:
            kwargs["weight"] = weight
        if priority:
            kwargs["priority"] = priority
        return await self._make_request(method, url, **kwargs)

    # Context manager support

    async def __aenter__(self):
        """Async context manager entry."""
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def aclose(self) -> None:
        """Close the client."""
        await self._client.aclose()

    # Properties

    @property
    def headers(self) -> httpx.Headers:
        """Get client headers."""
        return self._client.headers

    @headers.setter
    def headers(self, value: Dict[str, str]) -> None:
        """Set client headers."""
        self._client.headers = httpx.Headers(value)

    @property
    def base_url(self) -> httpx.URL:
        """Get base URL."""
        return self._client.base_url

    @base_url.setter
    def base_url(self, value: Union[str, httpx.URL]) -> None:
        """Set base URL."""
        self._client.base_url = httpx.URL(value)


class RateLimitError(Exception):
    """Exception raised when rate limit cannot be acquired."""

    def __init__(self, message: str, exchange: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.exchange = exchange
        self.retry_after = retry_after


class ExchangeClientFactory:
    """Factory for creating pre-configured rate-limited clients.

    Creates clients with exchange-specific configurations.

    Example:
        >>> factory = ExchangeClientFactory(coordinator)
        >>> binance_client = factory.create_binance_client(api_key, secret)
        >>> coinbase_client = factory.create_coinbase_client(api_key, secret)
    """

    def __init__(self, coordinator: Optional[GlobalRateCoordinator] = None):
        self._coordinator = coordinator or GlobalRateCoordinator.get_instance()

    def create_binance_client(
        self, api_key: str, api_secret: str, sandbox: bool = True, **kwargs
    ) -> RateLimitedClient:
        """Create Binance-specific client."""
        base_url = "https://testnet.binance.vision" if sandbox else "https://api.binance.com"

        # Default Binance weights
        weights = {
            "/api/v3/ping": 1,
            "/api/v3/time": 1,
            "/api/v3/exchangeInfo": 20,
            "/api/v3/depth": 25,
            "/api/v3/trades": 5,
            "/api/v3/historicalTrades": 5,
            "/api/v3/aggTrades": 2,
            "/api/v3/klines": 2,
            "/api/v3/avgPrice": 2,
            "/api/v3/ticker/24hr": 40,
            "/api/v3/ticker/price": 2,
            "/api/v3/ticker/bookTicker": 2,
            "/api/v3/order": 1,
            "/api/v3/order/test": 1,
            "/api/v3/openOrders": 3,
            "/api/v3/allOrders": 10,
            "/api/v3/account": 20,
            "/api/v3/myTrades": 10,
        }

        client = RateLimitedClient(
            exchange="binance",
            coordinator=self._coordinator,
            base_url=base_url,
            exchange_type="binance",
            headers={
                "X-MBX-APIKEY": api_key,
            },
            **kwargs,
        )
        client.set_weights(weights)

        return client

    def create_coinbase_client(
        self, api_key: str, api_secret: str, sandbox: bool = True, **kwargs
    ) -> RateLimitedClient:
        """Create Coinbase-specific client."""
        base_url = (
            "https://api-public.sandbox.pro.coinbase.com"
            if sandbox
            else "https://api.exchange.coinbase.com"
        )

        client = RateLimitedClient(
            exchange="coinbase",
            coordinator=self._coordinator,
            base_url=base_url,
            exchange_type="coinbase",
            **kwargs,
        )

        return client

    def create_kraken_client(self, api_key: str, api_secret: str, **kwargs) -> RateLimitedClient:
        """Create Kraken-specific client."""
        client = RateLimitedClient(
            exchange="kraken",
            coordinator=self._coordinator,
            base_url="https://api.kraken.com",
            exchange_type="kraken",
            **kwargs,
        )

        return client


def create_default_weights(exchange: str) -> Dict[str, int]:
    """Get default request weights for an exchange.

    Args:
        exchange: Exchange name

    Returns:
        Dictionary mapping paths to weights
    """
    weights = {
        "binance": {
            "/api/v3/ping": 1,
            "/api/v3/time": 1,
            "/api/v3/exchangeInfo": 20,
            "/api/v3/depth": 25,
            "/api/v3/trades": 5,
            "/api/v3/aggTrades": 2,
            "/api/v3/klines": 2,
            "/api/v3/ticker/24hr": 40,
            "/api/v3/ticker/price": 2,
            "/api/v3/order": 1,
            "/api/v3/order/test": 1,
            "/api/v3/openOrders": 3,
            "/api/v3/allOrders": 10,
            "/api/v3/account": 20,
            "/api/v3/myTrades": 10,
        },
        "coinbase": {
            "/products": 1,
            "/products/*/book": 1,
            "/products/*/ticker": 1,
            "/products/*/trades": 1,
            "/products/*/candles": 1,
            "/products/*/stats": 1,
            "/orders": 1,
            "/orders/*": 1,
        },
        "kraken": {
            "/0/public/Time": 1,
            "/0/public/Assets": 1,
            "/0/public/AssetPairs": 1,
            "/0/public/Ticker": 1,
            "/0/public/Depth": 1,
            "/0/public/Trades": 1,
            "/0/public/OHLC": 1,
            "/0/private/Balance": 1,
            "/0/private/TradeBalance": 1,
            "/0/private/OpenOrders": 1,
            "/0/private/ClosedOrders": 1,
            "/0/private/AddOrder": 1,
            "/0/private/CancelOrder": 1,
        },
    }

    return weights.get(exchange, {})
