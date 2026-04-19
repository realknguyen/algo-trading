"""Multi-level rate limiting for exchange API requests.

This module provides sophisticated rate limiting with:
- Per-endpoint rate limits
- Per-exchange global limits
- Weight-based limiting (Binance-style)
- Sliding window rate limiting
- Priority queuing for critical requests
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from urllib.parse import urlparse
import re

from src.rate_limiter.token_bucket import (
    TokenBucket,
    AdaptiveTokenBucket,
    Priority,
    TokenBucketGroup,
)


@dataclass
class EndpointConfig:
    """Configuration for an endpoint's rate limit.

    Attributes:
        pattern: URL pattern to match (regex or exact path)
        method: HTTP method (GET, POST, etc.) or None for all
        rate: Requests per second
        burst: Burst capacity
        weight: Request weight for weighted rate limiting
        priority: Default priority for this endpoint
    """

    pattern: str
    method: Optional[str] = None
    rate: float = 10.0
    burst: float = 20.0
    weight: float = 1.0
    priority: Priority = Priority.NORMAL

    def matches(self, path: str, method: str) -> bool:
        """Check if request matches this endpoint config."""
        if self.method and self.method.upper() != method.upper():
            return False

        # Try exact match first
        if self.pattern == path:
            return True

        # Try regex match
        try:
            return bool(re.search(self.pattern, path))
        except re.error:
            # If pattern isn't valid regex, do simple contains check
            return self.pattern in path


@dataclass
class ExchangeLimitConfig:
    """Rate limit configuration for an exchange.

    Supports both simple and weighted rate limiting.

    Binance-style weighted example:
        - Request weight limits (e.g., 1200 per minute)
        - Order placement limits (e.g., 50 orders per 10 seconds)
        - Raw request limits (e.g., 6000 per minute)

    Attributes:
        exchange_name: Name of the exchange
        global_rate: Global requests per second
        global_burst: Global burst capacity
        order_rate: Order placement rate limit
        order_burst: Order placement burst
        raw_rate: Raw request rate (unweighted)
        raw_burst: Raw request burst
        weight_limit: Maximum weight per time window (for weighted limiting)
        weight_window: Time window for weight limit in seconds
    """

    exchange_name: str

    # Global limits
    global_rate: float = 10.0
    global_burst: float = 20.0

    # Order-specific limits
    order_rate: float = 2.0
    order_burst: float = 5.0

    # Raw request limits (unweighted)
    raw_rate: Optional[float] = None
    raw_burst: Optional[float] = None

    # Weight-based limits
    weight_limit: Optional[float] = None  # e.g., 1200
    weight_window: float = 60.0  # e.g., 60 seconds

    # Endpoint-specific configs
    endpoints: List[EndpointConfig] = field(default_factory=list)

    def get_endpoint_config(self, path: str, method: str) -> Optional[EndpointConfig]:
        """Get matching endpoint config for a request."""
        for config in self.endpoints:
            if config.matches(path, method):
                return config
        return None


class RateLimiterBackend(ABC):
    """Abstract base for rate limiter storage backends."""

    @abstractmethod
    async def get_counter(self, key: str) -> float:
        """Get current counter value."""
        pass

    @abstractmethod
    async def increment(self, key: str, amount: float = 1.0, ttl: Optional[float] = None) -> float:
        """Increment counter and return new value."""
        pass

    @abstractmethod
    async def set_counter(self, key: str, value: float, ttl: Optional[float] = None) -> None:
        """Set counter value."""
        pass

    @abstractmethod
    async def get_window_requests(self, key: str, window_start: float) -> List[float]:
        """Get request timestamps within a sliding window."""
        pass

    @abstractmethod
    async def add_window_request(self, key: str, timestamp: float, ttl: float) -> None:
        """Add request timestamp for sliding window."""
        pass


class InMemoryBackend(RateLimiterBackend):
    """In-memory storage backend for rate limiting."""

    def __init__(self):
        self._counters: Dict[str, Tuple[float, Optional[float]]] = {}  # value, expiry
        self._windows: Dict[str, List[Tuple[float, float]]] = {}  # timestamp, expiry
        self._lock = asyncio.Lock()

    def _cleanup_expired(self, now: float) -> None:
        """Remove expired entries."""
        # Clean counters
        expired_keys = [k for k, (_, expiry) in self._counters.items() if expiry and now > expiry]
        for k in expired_keys:
            del self._counters[k]

        # Clean windows
        for key in self._windows:
            self._windows[key] = [(ts, exp) for ts, exp in self._windows[key] if now <= exp]

    async def get_counter(self, key: str) -> float:
        async with self._lock:
            now = time.time()
            self._cleanup_expired(now)
            return self._counters.get(key, (0.0, None))[0]

    async def increment(self, key: str, amount: float = 1.0, ttl: Optional[float] = None) -> float:
        async with self._lock:
            now = time.time()
            self._cleanup_expired(now)

            current = self._counters.get(key, (0.0, None))[0]
            new_value = current + amount
            expiry = now + ttl if ttl else None
            self._counters[key] = (new_value, expiry)
            return new_value

    async def set_counter(self, key: str, value: float, ttl: Optional[float] = None) -> None:
        async with self._lock:
            expiry = time.time() + ttl if ttl else None
            self._counters[key] = (value, expiry)

    async def get_window_requests(self, key: str, window_start: float) -> List[float]:
        async with self._lock:
            now = time.time()
            self._cleanup_expired(now)

            if key not in self._windows:
                return []

            return [ts for ts, _ in self._windows[key] if ts >= window_start]

    async def add_window_request(self, key: str, timestamp: float, ttl: float) -> None:
        async with self._lock:
            if key not in self._windows:
                self._windows[key] = []
            expiry = timestamp + ttl
            self._windows[key].append((timestamp, expiry))


class SlidingWindowLimiter:
    """Sliding window rate limiter implementation.

    Tracks request timestamps within a time window and enforces limits.
    More precise than token bucket for strict rate limiting.

    Example:
        >>> limiter = SlidingWindowLimiter(limit=100, window=60.0)
        >>> if await limiter.acquire():
        ...     # Make request
    """

    def __init__(
        self,
        limit: int,
        window: float,
        backend: Optional[RateLimiterBackend] = None,
        key_prefix: str = "sliding",
    ):
        """Initialize sliding window limiter.

        Args:
            limit: Maximum requests per window
            window: Time window in seconds
            backend: Storage backend (default: in-memory)
            key_prefix: Prefix for storage keys
        """
        self._limit = limit
        self._window = window
        self._backend = backend or InMemoryBackend()
        self._key_prefix = key_prefix

    @property
    def limit(self) -> int:
        return self._limit

    @limit.setter
    def limit(self, value: int) -> None:
        self._limit = value

    @property
    def window(self) -> float:
        return self._window

    def _make_key(self, identifier: str = "default") -> str:
        return f"{self._key_prefix}:{identifier}"

    async def acquire(
        self, identifier: str = "default", tokens: int = 1, timeout: Optional[float] = None
    ) -> bool:
        """Try to acquire slot in sliding window.

        Args:
            identifier: Unique identifier for this window (e.g., user_id, ip)
            tokens: Number of slots to acquire
            timeout: Maximum time to wait

        Returns:
            True if acquired, False otherwise
        """
        start_time = time.time()
        key = self._make_key(identifier)

        while True:
            now = time.time()
            window_start = now - self._window

            # Get requests in current window
            requests = await self._backend.get_window_requests(key, window_start)

            if len(requests) + tokens <= self._limit:
                # Acquire slots
                for _ in range(tokens):
                    await self._backend.add_window_request(key, now, self._window * 2)
                return True

            # Check timeout
            if timeout is not None and (time.time() - start_time) >= timeout:
                return False

            # Wait until oldest request expires
            if requests:
                oldest = min(requests)
                wait_time = (oldest + self._window) - now
                if wait_time > 0:
                    wait_time = min(wait_time, 0.1)  # Max 100ms between checks
                    await asyncio.sleep(wait_time)
                else:
                    await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(0.01)

    async def get_remaining(self, identifier: str = "default") -> int:
        """Get remaining slots in current window."""
        key = self._make_key(identifier)
        window_start = time.time() - self._window
        requests = await self._backend.get_window_requests(key, window_start)
        return max(0, self._limit - len(requests))

    async def get_reset_time(self, identifier: str = "default") -> float:
        """Get timestamp when window will have space."""
        key = self._make_key(identifier)
        window_start = time.time() - self._window
        requests = await self._backend.get_window_requests(key, window_start)

        if len(requests) < self._limit:
            return time.time()

        oldest = min(requests)
        return oldest + self._window


class RateLimiter:
    """Multi-level rate limiter for exchange API requests.

    Manages rate limiting at multiple levels:
    1. Global exchange limits (all requests)
    2. Endpoint-specific limits (per URL pattern)
    3. Weight-based limits (Binance-style)
    4. Order-specific limits (placement/cancellation)

    Example:
        >>> config = ExchangeLimitConfig(
        ...     exchange_name="binance",
        ...     global_rate=20.0,
        ...     weight_limit=1200,
        ...     weight_window=60.0
        ... )
        >>> limiter = RateLimiter(config)
        >>> await limiter.acquire("/api/v3/order", "POST", weight=10)
    """

    def __init__(
        self,
        config: ExchangeLimitConfig,
        backend: Optional[RateLimiterBackend] = None,
        enable_sliding_window: bool = False,
    ):
        """Initialize multi-level rate limiter.

        Args:
            config: Exchange rate limit configuration
            backend: Storage backend for distributed limiting
            enable_sliding_window: Use sliding window instead of token bucket
        """
        self._config = config
        self._backend = backend or InMemoryBackend()
        self._enable_sliding_window = enable_sliding_window

        # Initialize buckets
        self._init_buckets()

        # Track active requests for metrics
        self._active_requests = 0
        self._total_requests = 0
        self._throttled_requests = 0

    def _init_buckets(self) -> None:
        """Initialize token buckets for different limit types."""
        # Global rate limiter
        if self._enable_sliding_window:
            self._global_limiter = SlidingWindowLimiter(
                limit=int(self._config.global_rate * 60),
                window=60.0,
                backend=self._backend,
                key_prefix=f"{self._config.exchange_name}:global",
            )
        else:
            self._global_bucket = AdaptiveTokenBucket(
                rate=self._config.global_rate,
                capacity=self._config.global_burst,
                name=f"{self._config.exchange_name}_global",
            )

        # Order-specific limiter
        self._order_bucket = AdaptiveTokenBucket(
            rate=self._config.order_rate,
            capacity=self._config.order_burst,
            name=f"{self._config.exchange_name}_orders",
        )

        # Raw request limiter (optional)
        if self._config.raw_rate:
            self._raw_bucket = AdaptiveTokenBucket(
                rate=self._config.raw_rate,
                capacity=self._config.raw_burst or self._config.raw_rate * 2,
                name=f"{self._config.exchange_name}_raw",
            )
        else:
            self._raw_bucket = None

        # Weight-based limiter (Binance-style)
        if self._config.weight_limit:
            self._weight_limiter = SlidingWindowLimiter(
                limit=int(self._config.weight_limit),
                window=self._config.weight_window,
                backend=self._backend,
                key_prefix=f"{self._config.exchange_name}:weight",
            )
        else:
            self._weight_limiter = None

        # Endpoint-specific buckets
        self._endpoint_buckets: Dict[str, TokenBucket] = {}
        for endpoint in self._config.endpoints:
            bucket_name = f"{self._config.exchange_name}:{endpoint.pattern}"
            self._endpoint_buckets[bucket_name] = AdaptiveTokenBucket(
                rate=endpoint.rate, capacity=endpoint.burst, name=bucket_name
            )

    @property
    def exchange_name(self) -> str:
        return self._config.exchange_name

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get rate limiter metrics."""
        metrics = {
            "exchange": self._config.exchange_name,
            "active_requests": self._active_requests,
            "total_requests": self._total_requests,
            "throttled_requests": self._throttled_requests,
            "global": self._get_global_metrics(),
            "orders": self._order_bucket.adaptive_metrics,
        }

        if self._raw_bucket:
            metrics["raw"] = self._raw_bucket.adaptive_metrics

        if self._weight_limiter:
            metrics["weight_remaining"] = asyncio.run_coroutine_threadsafe(
                self._weight_limiter.get_remaining(), asyncio.get_event_loop()
            ).result()

        return metrics

    def _get_global_metrics(self) -> Dict[str, Any]:
        """Get global limiter metrics."""
        if self._enable_sliding_window:
            return {
                "type": "sliding_window",
                "limit": self._global_limiter.limit,
                "window": self._global_limiter.window,
            }
        else:
            return {
                "type": "token_bucket",
                **self._global_bucket.adaptive_metrics,
            }

    def _is_order_endpoint(self, path: str, method: str) -> bool:
        """Check if this is an order-related endpoint."""
        order_patterns = [
            r"/order",
            r"/orders",
            r"/trade",
            r"/batch",
        ]
        return any(re.search(pattern, path, re.IGNORECASE) for pattern in order_patterns)

    def _get_endpoint_bucket(self, path: str, method: str) -> Optional[TokenBucket]:
        """Get matching endpoint-specific bucket."""
        endpoint_config = self._config.get_endpoint_config(path, method)
        if endpoint_config:
            bucket_name = f"{self._config.exchange_name}:{endpoint_config.pattern}"
            return self._endpoint_buckets.get(bucket_name)
        return None

    async def acquire(
        self,
        path: str,
        method: str = "GET",
        weight: float = 1.0,
        priority: Optional[Priority] = None,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> bool:
        """Acquire rate limit for a request.

        Args:
            path: API endpoint path
            method: HTTP method
            weight: Request weight (for weighted limiting)
            priority: Request priority (overrides endpoint default)
            timeout: Maximum wait time

        Returns:
            True if all limits acquired
        """
        self._total_requests += 1
        start_time = time.time()

        # Get endpoint config for priority
        endpoint_config = self._config.get_endpoint_config(path, method)
        if priority is None and endpoint_config:
            priority = endpoint_config.priority
        priority = priority or Priority.NORMAL

        try:
            # Acquire global limit
            if self._enable_sliding_window:
                if not await self._global_limiter.acquire(tokens=int(weight), timeout=timeout):
                    self._throttled_requests += 1
                    return False
            else:
                if not await self._global_bucket.acquire(
                    tokens=weight, priority=priority, timeout=timeout
                ):
                    self._throttled_requests += 1
                    return False

            # Acquire order limit if applicable
            if self._is_order_endpoint(path, method):
                order_timeout = timeout
                if timeout:
                    elapsed = time.time() - start_time
                    order_timeout = max(0, timeout - elapsed)

                if not await self._order_bucket.acquire(
                    tokens=1, priority=priority, timeout=order_timeout
                ):
                    self._throttled_requests += 1
                    return False

            # Acquire raw request limit if configured
            if self._raw_bucket:
                raw_timeout = timeout
                if timeout:
                    elapsed = time.time() - start_time
                    raw_timeout = max(0, timeout - elapsed)

                if not await self._raw_bucket.acquire(
                    tokens=1, priority=priority, timeout=raw_timeout
                ):
                    self._throttled_requests += 1
                    return False

            # Acquire weight limit if configured (Binance-style)
            if self._weight_limiter and weight > 0:
                weight_timeout = timeout
                if timeout:
                    elapsed = time.time() - start_time
                    weight_timeout = max(0, timeout - elapsed)

                if not await self._weight_limiter.acquire(
                    tokens=int(weight), timeout=weight_timeout
                ):
                    self._throttled_requests += 1
                    return False

            # Acquire endpoint-specific limit
            endpoint_bucket = self._get_endpoint_bucket(path, method)
            if endpoint_bucket:
                ep_timeout = timeout
                if timeout:
                    elapsed = time.time() - start_time
                    ep_timeout = max(0, timeout - elapsed)

                if not await endpoint_bucket.acquire(
                    tokens=1, priority=priority, timeout=ep_timeout
                ):
                    self._throttled_requests += 1
                    return False

            self._active_requests += 1
            return True

        except asyncio.TimeoutError:
            self._throttled_requests += 1
            raise

    def release(self, path: str, method: str = "GET") -> None:
        """Release rate limit (for concurrency tracking)."""
        self._active_requests = max(0, self._active_requests - 1)

    async def update_config(self, config: ExchangeLimitConfig) -> None:
        """Update rate limit configuration dynamically."""
        self._config = config

        # Update global bucket
        if not self._enable_sliding_window:
            self._global_bucket.rate = config.global_rate
            self._global_bucket.capacity = config.global_burst

        # Update order bucket
        self._order_bucket.rate = config.order_rate
        self._order_bucket.capacity = config.order_burst

        # Update raw bucket
        if self._raw_bucket and config.raw_rate:
            self._raw_bucket.rate = config.raw_rate
            self._raw_bucket.capacity = config.raw_burst or config.raw_rate * 2

        # Update weight limiter
        if self._weight_limiter and config.weight_limit:
            self._weight_limiter.limit = int(config.weight_limit)
            self._weight_limiter.window = config.weight_window

    async def record_rate_limit_hit(self, retry_after: Optional[float] = None) -> None:
        """Record that we hit a rate limit (adaptive adjustment)."""
        # Reduce rates on all buckets
        if not self._enable_sliding_window:
            self._global_bucket.record_rate_limit(retry_after)
        self._order_bucket.record_rate_limit(retry_after)
        if self._raw_bucket:
            self._raw_bucket.record_rate_limit(retry_after)

    async def record_success(self, path: str, response_time: float) -> None:
        """Record successful request (adaptive adjustment)."""
        if not self._enable_sliding_window:
            self._global_bucket.record_success(response_time)
        self._order_bucket.record_success(response_time)
        if self._raw_bucket:
            self._raw_bucket.record_success(response_time)

        endpoint_bucket = self._get_endpoint_bucket(path, "GET")  # Generic lookup
        if endpoint_bucket and isinstance(endpoint_bucket, AdaptiveTokenBucket):
            endpoint_bucket.record_success(response_time)

    async def shutdown(self) -> None:
        """Shutdown the rate limiter."""
        if not self._enable_sliding_window:
            await self._global_bucket.shutdown()
        await self._order_bucket.shutdown()
        if self._raw_bucket:
            await self._raw_bucket.shutdown()
        for bucket in self._endpoint_buckets.values():
            await bucket.shutdown()


class MultiExchangeRateLimiter:
    """Manages rate limiters for multiple exchanges.

    Example:
        >>> multi = MultiExchangeRateLimiter()
        >>> multi.add_exchange(binance_config)
        >>> multi.add_exchange(coinbase_config)
        >>> await multi.acquire("binance", "/api/v3/order", "POST")
    """

    def __init__(self, backend: Optional[RateLimiterBackend] = None):
        self._limiters: Dict[str, RateLimiter] = {}
        self._backend = backend or InMemoryBackend()
        self._lock = asyncio.Lock()

    def add_exchange(
        self, config: ExchangeLimitConfig, enable_sliding_window: bool = False
    ) -> RateLimiter:
        """Add an exchange rate limiter."""
        limiter = RateLimiter(config, self._backend, enable_sliding_window)
        self._limiters[config.exchange_name] = limiter
        return limiter

    def get_limiter(self, exchange: str) -> Optional[RateLimiter]:
        """Get rate limiter for an exchange."""
        return self._limiters.get(exchange)

    async def acquire(self, exchange: str, path: str, method: str = "GET", **kwargs) -> bool:
        """Acquire rate limit for an exchange request."""
        limiter = self._limiters.get(exchange)
        if not limiter:
            raise KeyError(f"No rate limiter configured for exchange: {exchange}")
        return await limiter.acquire(path, method, **kwargs)

    def release(self, exchange: str, path: str, method: str = "GET") -> None:
        """Release rate limit."""
        limiter = self._limiters.get(exchange)
        if limiter:
            limiter.release(path, method)

    @property
    def exchanges(self) -> List[str]:
        """List configured exchanges."""
        return list(self._limiters.keys())

    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all exchanges."""
        return {name: limiter.metrics for name, limiter in self._limiters.items()}

    async def shutdown_all(self) -> None:
        """Shutdown all rate limiters."""
        for limiter in self._limiters.values():
            await limiter.shutdown()
        self._limiters.clear()
