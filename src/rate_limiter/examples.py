"""Example usage of the rate limiting system.

This file demonstrates how to use the rate limiting system in various scenarios.
"""

import asyncio
from decimal import Decimal

from src.rate_limiter import (
    # Core components
    TokenBucket,
    AdaptiveTokenBucket,
    Priority,
    
    # Rate limiter
    RateLimiter,
    MultiExchangeRateLimiter,
    ExchangeLimitConfig,
    EndpointConfig,
    SlidingWindowLimiter,
    
    # Coordinator
    GlobalRateCoordinator,
    get_coordinator,
    RateLimitExceeded,
    
    # Storage
    create_storage_backend,
    RateLimitStateManager,
    
    # HTTP Client
    RateLimitedClient,
    ExchangeClientFactory,
)


async def example_token_bucket():
    """Example: Basic token bucket usage."""
    print("\n=== Token Bucket Example ===")
    
    # Create a token bucket: 10 tokens/sec, burst of 20
    bucket = TokenBucket(rate=10.0, capacity=20.0, name="api_requests")
    
    # Acquire tokens
    print("Acquiring 5 tokens...")
    await bucket.acquire(tokens=5.0)
    print(f"Remaining tokens: {bucket.tokens:.2f}")
    
    # Acquire with priority
    print("\nAcquiring with HIGH priority...")
    await bucket.acquire(tokens=3.0, priority=Priority.HIGH)
    print(f"Remaining tokens: {bucket.tokens:.2f}")
    
    # Try to acquire without waiting
    can_acquire = bucket.try_acquire(tokens=20.0)
    print(f"\nCan acquire 20 tokens without waiting? {can_acquire}")
    
    # Shutdown
    await bucket.shutdown()
    print("Bucket shutdown.")


async def example_adaptive_bucket():
    """Example: Adaptive token bucket that adjusts based on feedback."""
    print("\n=== Adaptive Token Bucket Example ===")
    
    bucket = AdaptiveTokenBucket(
        rate=100.0,
        capacity=200.0,
        min_rate=10.0,
        max_rate=500.0,
        name="adaptive_api"
    )
    
    # Simulate some requests
    for i in range(10):
        await bucket.acquire()
        bucket.record_success(response_time=0.05)
    
    print(f"Rate after successes: {bucket.rate:.2f}")
    
    # Simulate rate limit hit
    bucket.record_rate_limit(retry_after=60)
    print(f"Rate after rate limit: {bucket.rate:.2f}")
    
    print(f"\nMetrics: {bucket.adaptive_metrics}")
    
    await bucket.shutdown()


async def example_exchange_limiter():
    """Example: Multi-level rate limiting for an exchange."""
    print("\n=== Exchange Rate Limiter Example ===")
    
    # Configure rate limits for Binance-style API
    config = ExchangeLimitConfig(
        exchange_name="binance",
        global_rate=20.0,      # 20 requests per second globally
        global_burst=40.0,
        order_rate=5.0,        # 5 orders per second
        order_burst=10.0,
        weight_limit=1200,     # 1200 weight per minute (Binance style)
        weight_window=60.0,
        endpoints=[
            EndpointConfig(
                pattern=r"/api/v3/order",
                method="POST",
                rate=5.0,
                burst=10.0,
                weight=10,
                priority=Priority.HIGH
            ),
            EndpointConfig(
                pattern=r"/api/v3/account",
                rate=2.0,
                burst=5.0,
                weight=20
            ),
        ]
    )
    
    # Create rate limiter
    limiter = RateLimiter(config)
    
    # Acquire for different endpoints
    print("Acquiring for account endpoint...")
    acquired = await limiter.acquire("/api/v3/account", "GET", weight=20)
    print(f"Acquired: {acquired}")
    
    print("\nAcquiring for order placement...")
    acquired = await limiter.acquire("/api/v3/order", "POST", weight=10)
    print(f"Acquired: {acquired}")
    
    # Show metrics
    print(f"\nMetrics: {limiter.metrics}")
    
    await limiter.shutdown()


async def example_coordinator():
    """Example: Global rate coordinator."""
    print("\n=== Global Coordinator Example ===")
    
    # Get singleton coordinator
    coordinator = get_coordinator()
    
    # Configure Binance
    binance_config = ExchangeLimitConfig(
        exchange_name="binance",
        global_rate=20.0,
        weight_limit=1200,
        weight_window=60.0
    )
    coordinator.register_exchange("binance", binance_config)
    
    # Configure Coinbase
    coinbase_config = ExchangeLimitConfig(
        exchange_name="coinbase",
        global_rate=10.0,
        global_burst=20.0
    )
    coordinator.register_exchange("coinbase", coinbase_config)
    
    # Start background tasks
    await coordinator.start()
    
    # Make rate-limited requests
    print("Making requests to multiple exchanges...")
    
    # Request to Binance with high priority
    acquired = await coordinator.acquire(
        "binance",
        "/api/v3/order",
        "POST",
        weight=10,
        priority=Priority.HIGH
    )
    print(f"Binance order request acquired: {acquired}")
    coordinator.release("binance", "/api/v3/order", "POST")
    
    # Request to Coinbase
    acquired = await coordinator.acquire(
        "coinbase",
        "/products",
        "GET",
        priority=Priority.NORMAL
    )
    print(f"Coinbase products request acquired: {acquired}")
    coordinator.release("coinbase", "/products", "GET")
    
    # Show metrics
    print(f"\nCoordinator metrics: {coordinator.metrics}")
    
    # Shutdown
    await coordinator.shutdown()
    print("Coordinator shutdown.")


async def example_context_manager():
    """Example: Using context managers for rate limiting."""
    print("\n=== Context Manager Example ===")
    
    coordinator = get_coordinator()
    
    # Register exchange
    config = ExchangeLimitConfig(
        exchange_name="example",
        global_rate=10.0
    )
    coordinator.register_exchange("example", config)
    await coordinator.start()
    
    # Use context manager
    try:
        async with coordinator.request(
            "example",
            "/api/critical",
            "POST",
            weight=5,
            priority=Priority.CRITICAL
        ):
            print("Inside rate-limited context - making request...")
            await asyncio.sleep(0.1)  # Simulate request
            print("Request completed!")
    except RateLimitExceeded:
        print("Could not acquire rate limit!")
    
    await coordinator.shutdown()


async def example_storage():
    """Example: Persistent storage for rate limits."""
    print("\n=== Storage Example ===")
    
    # Create file-based storage
    storage = create_storage_backend("file", base_path="~/.ratelimit_example")
    
    # Create state manager
    manager = RateLimitStateManager(storage)
    
    # Create a bucket and use it
    bucket = TokenBucket(rate=10.0, capacity=20.0, name="test")
    await bucket.acquire(tokens=5.0)
    
    print(f"Bucket state: tokens={bucket.tokens:.2f}")
    
    # Save state
    await manager.save_token_bucket_state("test_exchange", "api", bucket)
    print("State saved.")
    
    # Restore state (simulating restart)
    restored = await manager.restore_token_bucket_state("test_exchange", "api")
    if restored:
        print(f"Restored bucket: tokens={restored.tokens:.2f}")
    
    await bucket.shutdown()
    
    # Clean up
    import shutil
    import os
    storage_path = os.path.expanduser("~/.ratelimit_example")
    if os.path.exists(storage_path):
        shutil.rmtree(storage_path)
    print("Cleanup complete.")


async def example_sliding_window():
    """Example: Sliding window rate limiting."""
    print("\n=== Sliding Window Example ===")
    
    # Create sliding window limiter: 100 requests per minute
    limiter = SlidingWindowLimiter(
        limit=100,
        window=60.0
    )
    
    # Acquire some slots
    for i in range(5):
        acquired = await limiter.acquire(tokens=1)
        print(f"Request {i+1}: acquired={acquired}")
    
    # Check remaining
    remaining = await limiter.get_remaining()
    print(f"\nRemaining slots: {remaining}")
    
    # Check reset time
    reset_time = await limiter.get_reset_time()
    print(f"Window resets at: {reset_time:.2f}")


async def example_multi_exchange():
    """Example: Managing multiple exchanges."""
    print("\n=== Multi-Exchange Example ===")
    
    # Create multi-exchange limiter
    multi = MultiExchangeRateLimiter()
    
    # Add exchanges
    multi.add_exchange(ExchangeLimitConfig(
        exchange_name="binance",
        global_rate=20.0
    ))
    
    multi.add_exchange(ExchangeLimitConfig(
        exchange_name="coinbase",
        global_rate=10.0
    ))
    
    multi.add_exchange(ExchangeLimitConfig(
        exchange_name="kraken",
        global_rate=5.0
    ))
    
    # Acquire from each
    for exchange in multi.exchanges:
        acquired = await multi.acquire(exchange, "/test", "GET")
        print(f"{exchange}: acquired={acquired}")
        multi.release(exchange, "/test", "GET")
    
    # Show all metrics
    print(f"\nAll metrics: {multi.get_all_metrics()}")
    
    await multi.shutdown_all()


async def example_priority_queue():
    """Example: Priority queuing for critical requests."""
    print("\n=== Priority Queue Example ===")
    
    coordinator = get_coordinator()
    
    # Register with low rate to force queuing
    config = ExchangeLimitConfig(
        exchange_name="priority_test",
        global_rate=2.0,  # Only 2 requests per second
        global_burst=2.0
    )
    coordinator.register_exchange("priority_test", config)
    await coordinator.start()
    
    # Launch multiple concurrent requests with different priorities
    async def make_request(name: str, priority: Priority):
        try:
            async with coordinator.request(
                "priority_test",
                "/api/action",
                "POST",
                priority=priority,
                timeout=5.0
            ):
                print(f"  [{name}] Started")
                await asyncio.sleep(0.1)
                print(f"  [{name}] Completed")
        except asyncio.TimeoutError:
            print(f"  [{name}] Timed out")
    
    print("Launching requests (LOW, NORMAL, HIGH, CRITICAL)...")
    
    # Launch all at once
    await asyncio.gather(
        make_request("LOW", Priority.LOW),
        make_request("NORMAL", Priority.NORMAL),
        make_request("HIGH", Priority.HIGH),
        make_request("CRITICAL", Priority.CRITICAL),
    )
    
    await coordinator.shutdown()


async def main():
    """Run all examples."""
    print("=" * 60)
    print("Rate Limiting System Examples")
    print("=" * 60)
    
    await example_token_bucket()
    await example_adaptive_bucket()
    await example_sliding_window()
    await example_exchange_limiter()
    await example_multi_exchange()
    await example_coordinator()
    await example_context_manager()
    await example_storage()
    await example_priority_queue()
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
