"""Rate limiting system for algorithmic trading.

This package provides sophisticated rate limiting for exchange API requests:

- **Token Bucket**: Async token bucket algorithm with fair queuing
- **Rate Limiter**: Multi-level rate limiting (per-endpoint, per-exchange)
- **Coordinator**: Global rate limit coordinator for cross-exchange management
- **Storage**: Persistence backends (Redis, in-memory, file)

Quick Start:
    >>> from src.rate_limiter import (
    ...     GlobalRateCoordinator,
    ...     ExchangeLimitConfig,
    ...     Priority,
    ... )

    >>> # Configure rate limits for an exchange
    >>> config = ExchangeLimitConfig(
    ...     exchange_name="binance",
    ...     global_rate=20.0,
    ...     global_burst=40.0,
    ...     weight_limit=1200,
    ...     weight_window=60.0
    ... )

    >>> # Get coordinator and register exchange
    >>> coordinator = GlobalRateCoordinator.get_instance()
    >>> coordinator.register_exchange("binance", config)

    >>> # Use context manager for rate-limited requests
    >>> async with coordinator.request("binance", "/api/v3/order", "POST", priority=Priority.HIGH):
    ...     # Your HTTP request here
    ...     response = await client.post(url)
    ...     coordinator.record_response("binance", response.headers, response.status_code)

Advanced Usage:
    >>> # Custom endpoint limits
    >>> from src.rate_limiter import EndpointConfig
    >>> config.endpoints = [
    ...     EndpointConfig(
    ...         pattern=r"/api/v3/order",
    ...         method="POST",
    ...         rate=5.0,
    ...         burst=10.0,
    ...         weight=10
    ...     )
    ... ]

    >>> # Distributed storage with Redis
    >>> from src.rate_limiter import create_storage_backend
    >>> storage = create_storage_backend("redis", redis_url="redis://localhost:6379")
    >>> coordinator = GlobalRateCoordinator(backend=storage)
"""

# Token Bucket
from src.rate_limiter.token_bucket import (
    TokenBucket,
    AdaptiveTokenBucket,
    TokenBucketGroup,
    Priority,
    WaitingRequest,
)

# Rate Limiter
from src.rate_limiter.rate_limiter import (
    RateLimiter,
    MultiExchangeRateLimiter,
    ExchangeLimitConfig,
    EndpointConfig,
    SlidingWindowLimiter,
    RateLimiterBackend,
    InMemoryBackend,
)

# Coordinator
from src.rate_limiter.coordinator import (
    GlobalRateCoordinator,
    RateLimitHeaders,
    ExchangeStatus,
    QueuedRequest,
    RateLimitExceeded,
    get_coordinator,
)

# Storage
from src.rate_limiter.storage import (
    StorageBackend,
    FileStorageBackend,
    RedisStorageBackend,
    HybridStorageBackend,
    RateLimitStateManager,
    RateLimitState,
    create_storage_backend,
)

# HTTP Client
from src.rate_limiter.http_client import (
    RateLimitedClient,
    ExchangeClientFactory,
    RateLimitError,
    create_default_weights,
)

# Integration
from src.rate_limiter.integration import (
    rate_limited,
    adapt_base_adapter,
    wrap_method_with_rate_limit,
    RateLimitMixin,
    create_middleware,
)

__all__ = [
    # Token Bucket
    "TokenBucket",
    "AdaptiveTokenBucket",
    "TokenBucketGroup",
    "Priority",
    "WaitingRequest",
    # Rate Limiter
    "RateLimiter",
    "MultiExchangeRateLimiter",
    "ExchangeLimitConfig",
    "EndpointConfig",
    "SlidingWindowLimiter",
    "RateLimiterBackend",
    "InMemoryBackend",
    # Coordinator
    "GlobalRateCoordinator",
    "RateLimitHeaders",
    "ExchangeStatus",
    "QueuedRequest",
    "RateLimitExceeded",
    "get_coordinator",
    # Storage
    "StorageBackend",
    "FileStorageBackend",
    "RedisStorageBackend",
    "HybridStorageBackend",
    "RateLimitStateManager",
    "RateLimitState",
    "create_storage_backend",
    # HTTP Client
    "RateLimitedClient",
    "ExchangeClientFactory",
    "RateLimitError",
    "create_default_weights",
    # Integration
    "rate_limited",
    "adapt_base_adapter",
    "wrap_method_with_rate_limit",
    "RateLimitMixin",
    "create_middleware",
]

__version__ = "1.0.0"
