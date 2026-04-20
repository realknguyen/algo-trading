"""Token bucket rate limiting algorithm with async support.

This module implements a high-performance token bucket algorithm with:
- Async acquire() with fair queuing
- Configurable rate and burst capacity
- Token refill scheduling
- Priority handling for critical requests
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, Dict, List
from enum import IntEnum
import heapq


class Priority(IntEnum):
    """Request priority levels.

    Higher numbers = higher priority (processed first).
    """

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass(order=True)
class WaitingRequest:
    """Represents a request waiting for tokens.

    Uses priority queue ordering where lower priority values come first.
    To make higher priority values come first, we negate the priority.
    """

    priority_neg: int  # Negated priority for min-heap (higher priority = lower number)
    timestamp: float = field(compare=True)  # Tie-breaker: earlier requests first
    tokens_needed: float = field(compare=False, default=1.0)
    future: asyncio.Future = field(compare=False)

    @property
    def priority(self) -> Priority:
        return Priority(-self.priority_neg)


class TokenBucket:
    """Async token bucket rate limiter with fair queuing.

    The token bucket algorithm allows for burst traffic up to the bucket capacity,
    while maintaining a steady average rate of tokens per second.

    Features:
    - Async acquire with timeout support
    - Fair queuing based on priority and arrival time
    - Dynamic rate adjustment
    - Token weight support (different operations cost different amounts)
    - Per-request priority handling

    Example:
        >>> bucket = TokenBucket(rate=10.0, capacity=20.0)
        >>> await bucket.acquire()  # Acquire 1 token
        >>> await bucket.acquire(tokens=5.0)  # Acquire 5 tokens
        >>> await bucket.acquire(priority=Priority.HIGH)  # High priority

    Attributes:
        rate: Tokens added per second
        capacity: Maximum tokens in bucket (burst capacity)
        tokens: Current available tokens
    """

    def __init__(
        self,
        rate: float,
        capacity: Optional[float] = None,
        initial_tokens: Optional[float] = None,
        name: str = "default",
    ):
        """Initialize token bucket.

        Args:
            rate: Tokens added per second
            capacity: Maximum bucket capacity (default: rate)
            initial_tokens: Starting tokens (default: capacity)
            name: Bucket identifier for logging/metrics
        """
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else rate)
        self._tokens = float(initial_tokens if initial_tokens is not None else self._capacity)
        self._name = name

        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self._waiters: List[WaitingRequest] = []  # Min-heap of waiting requests
        self._refill_task: Optional[asyncio.Task] = None
        self._shutdown = False

        # Metrics
        self._total_acquired = 0.0
        self._total_wait_time = 0.0
        self._max_wait_time = 0.0
        self._acquire_count = 0
        self._rejected_count = 0

    @property
    def rate(self) -> float:
        """Get current rate (tokens per second)."""
        return self._rate

    @rate.setter
    def rate(self, value: float) -> None:
        """Update the token refill rate dynamically."""
        self._rate = max(0.0, float(value))

    @property
    def capacity(self) -> float:
        """Get bucket capacity."""
        return self._capacity

    @capacity.setter
    def capacity(self, value: float) -> None:
        """Update bucket capacity dynamically."""
        self._capacity = max(0.0, float(value))
        # Adjust current tokens if they exceed new capacity
        self._tokens = min(self._tokens, self._capacity)

    @property
    def tokens(self) -> float:
        """Get current available tokens (not thread-safe, approximate)."""
        # Recalculate to give current value
        now = time.monotonic()
        elapsed = now - self._last_update
        return min(self._tokens + elapsed * self._rate, self._capacity)

    @property
    def utilization(self) -> float:
        """Get current bucket utilization (0.0 to 1.0)."""
        if self._capacity == 0:
            return 1.0
        return 1.0 - (self.tokens / self._capacity)

    @property
    def is_full(self) -> bool:
        """Check if bucket is at capacity."""
        return self.tokens >= self._capacity

    @property
    def is_empty(self) -> bool:
        """Check if bucket is empty."""
        return self.tokens <= 0

    @property
    def waiting_count(self) -> int:
        """Number of requests waiting for tokens."""
        return len(self._waiters)

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get bucket metrics."""
        avg_wait = self._total_wait_time / self._acquire_count if self._acquire_count > 0 else 0.0
        return {
            "name": self._name,
            "rate": self._rate,
            "capacity": self._capacity,
            "tokens": self.tokens,
            "utilization": self.utilization,
            "waiting": self.waiting_count,
            "total_acquired": self._total_acquired,
            "total_requests": self._acquire_count,
            "rejected": self._rejected_count,
            "avg_wait_time": avg_wait,
            "max_wait_time": self._max_wait_time,
        }

    def _refill(self) -> None:
        """Add tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._tokens + elapsed * self._rate, self._capacity)
        self._last_update = now

    def _can_acquire(self, tokens: float) -> bool:
        """Check if tokens can be acquired without waiting."""
        self._refill()
        return self._tokens >= tokens

    def _do_acquire(self, tokens: float) -> None:
        """Actually acquire tokens (internal, assumes refill done)."""
        self._tokens -= tokens
        self._total_acquired += tokens

    async def acquire(
        self,
        tokens: float = 1.0,
        priority: Priority = Priority.NORMAL,
        timeout: Optional[float] = None,
        wait: bool = True,
    ) -> bool:
        """Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire
            priority: Request priority (higher = processed sooner)
            timeout: Maximum time to wait for tokens (None = no timeout)
            wait: If False, return immediately if tokens unavailable

        Returns:
            True if tokens acquired, False if timeout or no wait

        Raises:
            asyncio.TimeoutError: If timeout expires before acquiring tokens
            RuntimeError: If bucket is shut down
        """
        if self._shutdown:
            raise RuntimeError("TokenBucket is shut down")

        tokens = float(tokens)
        if tokens <= 0:
            return True

        if tokens > self._capacity:
            raise ValueError(f"Cannot acquire {tokens} tokens (capacity: {self._capacity})")

        start_time = time.monotonic()

        async with self._lock:
            # Try immediate acquisition
            if self._can_acquire(tokens):
                self._do_acquire(tokens)
                self._acquire_count += 1
                return True

            if not wait:
                self._rejected_count += 1
                return False

        # Need to wait for tokens
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        request = WaitingRequest(
            priority_neg=-int(priority),  # Negate for min-heap
            timestamp=start_time,
            tokens_needed=tokens,
            future=future,
        )

        heapq.heappush(self._waiters, request)

        # Start refill processor if not running
        if self._refill_task is None or self._refill_task.done():
            self._refill_task = asyncio.create_task(self._process_waiters())

        try:
            if timeout is not None:
                await asyncio.wait_for(future, timeout=timeout)
            else:
                await future

            wait_time = time.monotonic() - start_time
            self._total_wait_time += wait_time
            self._max_wait_time = max(self._max_wait_time, wait_time)
            self._acquire_count += 1
            return True

        except asyncio.TimeoutError:
            # Remove from waiters
            async with self._lock:
                if request in self._waiters:
                    self._waiters.remove(request)
                    heapq.heapify(self._waiters)
            self._rejected_count += 1
            raise

    async def _process_waiters(self) -> None:
        """Background task to process waiting requests as tokens become available."""
        while self._waiters and not self._shutdown:
            async with self._lock:
                self._refill()

                # Process as many waiters as we can
                processed = []
                remaining_waiters = []

                for request in self._waiters:
                    if self._tokens >= request.tokens_needed:
                        self._do_acquire(request.tokens_needed)
                        if not request.future.done():
                            request.future.set_result(True)
                        processed.append(request)
                    else:
                        remaining_waiters.append(request)

                self._waiters = remaining_waiters
                heapq.heapify(self._waiters)

                if not self._waiters:
                    break

            # Wait a bit before checking again
            # Calculate optimal wait time based on rate and needed tokens
            if self._waiters:
                next_request = self._waiters[0]
                tokens_needed = next_request.tokens_needed - self.tokens
                if tokens_needed > 0 and self._rate > 0:
                    wait_time = min(tokens_needed / self._rate, 0.1)  # Max 100ms
                    await asyncio.sleep(wait_time)
                else:
                    await asyncio.sleep(0.01)  # Small delay to prevent busy-waiting

    async def acquire_many(
        self,
        count: int,
        tokens: float = 1.0,
        priority: Priority = Priority.NORMAL,
        timeout: Optional[float] = None,
    ) -> int:
        """Acquire tokens for multiple operations.

        Args:
            count: Number of operations to acquire for
            tokens: Tokens per operation
            priority: Request priority
            timeout: Timeout per acquisition

        Returns:
            Number of successful acquisitions
        """
        acquired = 0
        for _ in range(count):
            try:
                if await self.acquire(tokens, priority, timeout):
                    acquired += 1
            except asyncio.TimeoutError:
                break
        return acquired

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Try to acquire tokens without waiting (non-async).

        Returns:
            True if tokens were available and acquired
        """
        if self._shutdown:
            return False

        tokens = float(tokens)
        if tokens <= 0:
            return True

        # Check if we can acquire without async
        self._refill()
        if self._tokens >= tokens:
            self._do_acquire(tokens)
            self._acquire_count += 1
            return True
        return False

    def add_tokens(self, tokens: float) -> float:
        """Manually add tokens to the bucket.

        Args:
            tokens: Tokens to add

        Returns:
            Actual tokens added (capped by capacity)
        """
        tokens = float(tokens)
        if tokens <= 0:
            return 0.0

        self._refill()
        available = self._capacity - self._tokens
        to_add = min(tokens, available)
        self._tokens += to_add

        # Wake up processor if we have waiters
        if self._waiters and (self._refill_task is None or self._refill_task.done()):
            self._refill_task = asyncio.create_task(self._process_waiters())

        return to_add

    def drain(self, tokens: Optional[float] = None) -> float:
        """Remove tokens from the bucket.

        Args:
            tokens: Tokens to remove (None = drain all)

        Returns:
            Tokens actually removed
        """
        self._refill()
        if tokens is None:
            removed = self._tokens
            self._tokens = 0.0
        else:
            removed = min(self._tokens, float(tokens))
            self._tokens -= removed
        return removed

    async def reset(self) -> None:
        """Reset bucket to full capacity and clear all waiters."""
        async with self._lock:
            self._tokens = self._capacity
            self._last_update = time.monotonic()

            # Cancel all waiting requests
            for request in self._waiters:
                if not request.future.done():
                    request.future.set_exception(asyncio.CancelledError("Bucket reset"))
            self._waiters = []

    async def shutdown(self, wait: bool = True) -> None:
        """Shutdown the bucket and cleanup.

        Args:
            wait: If True, wait for current waiters to be processed
        """
        self._shutdown = True

        if wait and self._waiters:
            # Give some time for waiters to complete
            try:
                await asyncio.wait_for(self._wait_for_empty(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        # Cancel remaining waiters
        async with self._lock:
            for request in self._waiters:
                if not request.future.done():
                    request.future.set_exception(asyncio.CancelledError("Bucket shutdown"))
            self._waiters = []

        if self._refill_task and not self._refill_task.done():
            self._refill_task.cancel()
            try:
                await self._refill_task
            except asyncio.CancelledError:
                pass

    async def _wait_for_empty(self) -> None:
        """Wait for all waiters to be processed."""
        while self._waiters:
            await asyncio.sleep(0.1)

    def __repr__(self) -> str:
        return (
            f"TokenBucket("
            f"name='{self._name}', "
            f"rate={self._rate:.2f}, "
            f"capacity={self._capacity:.2f}, "
            f"tokens={self.tokens:.2f}, "
            f"waiting={self.waiting_count}"
            f")"
        )


class AdaptiveTokenBucket(TokenBucket):
    """Token bucket with adaptive rate based on feedback.

    Adjusts rate dynamically based on:
    - Success/failure rates
    - Response times
    - External rate limit feedback

    Example:
        >>> bucket = AdaptiveTokenBucket(rate=10.0, capacity=20.0)
        >>> bucket.record_success(response_time=0.1)
        >>> bucket.record_rate_limit(retry_after=60)
    """

    def __init__(
        self,
        rate: float,
        capacity: Optional[float] = None,
        min_rate: float = 0.1,
        max_rate: Optional[float] = None,
        adapt_factor: float = 0.8,
        **kwargs,
    ):
        """Initialize adaptive token bucket.

        Args:
            rate: Initial rate
            capacity: Bucket capacity
            min_rate: Minimum allowed rate
            max_rate: Maximum allowed rate (default: 10x initial)
            adapt_factor: Factor to multiply rate on adaptation (0-1)
        """
        super().__init__(rate, capacity, **kwargs)
        self._initial_rate = rate
        self._min_rate = min_rate
        self._max_rate = max_rate or rate * 10
        self._adapt_factor = adapt_factor

        self._success_count = 0
        self._failure_count = 0
        self._rate_limit_count = 0
        self._response_times: List[float] = []

    def record_success(self, response_time: Optional[float] = None) -> None:
        """Record a successful request.

        Args:
            response_time: Response time in seconds (for latency-based adaptation)
        """
        self._success_count += 1
        if response_time is not None:
            self._response_times.append(response_time)
            # Keep only last 100 response times
            if len(self._response_times) > 100:
                self._response_times = self._response_times[-100:]

        # Gradually increase rate on success
        if self._success_count % 10 == 0:
            new_rate = min(self._rate / self._adapt_factor, self._max_rate)
            self.rate = new_rate

    def record_failure(self) -> None:
        """Record a failed request (not rate limit)."""
        self._failure_count += 1

    def record_rate_limit(self, retry_after: Optional[float] = None) -> None:
        """Record hitting a rate limit.

        Args:
            retry_after: Seconds to wait before retry (if provided by server)
        """
        self._rate_limit_count += 1

        # Reduce rate significantly on rate limit
        if retry_after and retry_after > 0:
            # Calculate new rate based on retry_after
            # If we need to wait X seconds, reduce rate proportionally
            new_rate = max(self._rate * 0.5, self._min_rate)
        else:
            new_rate = max(self._rate * self._adapt_factor, self._min_rate)

        self.rate = new_rate

    @property
    def adaptive_metrics(self) -> Dict[str, Any]:
        """Get adaptive bucket specific metrics."""
        avg_response = (
            sum(self._response_times) / len(self._response_times) if self._response_times else 0.0
        )
        total = self._success_count + self._failure_count + self._rate_limit_count
        success_rate = self._success_count / total if total > 0 else 1.0

        return {
            **self.metrics,
            "initial_rate": self._initial_rate,
            "min_rate": self._min_rate,
            "max_rate": self._max_rate,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "rate_limit_count": self._rate_limit_count,
            "success_rate": success_rate,
            "avg_response_time": avg_response,
        }


class TokenBucketGroup:
    """Manages multiple named token buckets.

    Useful for per-endpoint or per-resource rate limiting.

    Example:
        >>> group = TokenBucketGroup()
        >>> group.create_bucket("api_read", rate=100)
        >>> group.create_bucket("api_write", rate=10)
        >>> await group.acquire("api_read")
    """

    def __init__(self):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    def create_bucket(
        self,
        name: str,
        rate: float,
        capacity: Optional[float] = None,
        adaptive: bool = False,
        **kwargs,
    ) -> TokenBucket:
        """Create a new bucket.

        Args:
            name: Bucket identifier
            rate: Token rate
            capacity: Bucket capacity
            adaptive: Use AdaptiveTokenBucket instead
            **kwargs: Additional arguments for bucket constructor

        Returns:
            Created bucket
        """
        bucket_class = AdaptiveTokenBucket if adaptive else TokenBucket
        bucket = bucket_class(rate=rate, capacity=capacity, name=name, **kwargs)
        self._buckets[name] = bucket
        return bucket

    def get_bucket(self, name: str) -> Optional[TokenBucket]:
        """Get a bucket by name."""
        return self._buckets.get(name)

    async def acquire(self, bucket_name: str, tokens: float = 1.0, **kwargs) -> bool:
        """Acquire tokens from a specific bucket."""
        bucket = self._buckets.get(bucket_name)
        if bucket is None:
            raise KeyError(f"Bucket '{bucket_name}' not found")
        return await bucket.acquire(tokens, **kwargs)

    async def acquire_from_any(self, bucket_names: List[str], tokens: float = 1.0, **kwargs) -> str:
        """Acquire from first available bucket.

        Returns:
            Name of bucket that granted tokens
        """
        for name in bucket_names:
            bucket = self._buckets.get(name)
            if bucket and bucket.try_acquire(tokens):
                return name

        # If none available, wait on first
        if bucket_names:
            await self.acquire(bucket_names[0], tokens, **kwargs)
            return bucket_names[0]

        raise ValueError("No bucket names provided")

    def remove_bucket(self, name: str) -> Optional[TokenBucket]:
        """Remove and return a bucket."""
        return self._buckets.pop(name, None)

    @property
    def bucket_names(self) -> List[str]:
        """List all bucket names."""
        return list(self._buckets.keys())

    def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all buckets."""
        return {name: bucket.metrics for name, bucket in self._buckets.items()}

    async def reset_all(self) -> None:
        """Reset all buckets."""
        for bucket in self._buckets.values():
            await bucket.reset()

    async def shutdown_all(self) -> None:
        """Shutdown all buckets."""
        for bucket in self._buckets.values():
            await bucket.shutdown()
        self._buckets.clear()
