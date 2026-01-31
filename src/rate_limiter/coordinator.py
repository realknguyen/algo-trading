"""Global rate limit coordinator for cross-exchange management.

Provides:
- Singleton coordinator for system-wide rate limit management
- Cross-exchange rate limit tracking
- Priority queuing for critical requests
- Rate limit header parsing (X-RateLimit-Remaining, etc.)
- Dynamic rate adjustment based on responses
"""

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from enum import IntEnum
import heapq
import re
from urllib.parse import urlparse

from src.rate_limiter.token_bucket import TokenBucket, AdaptiveTokenBucket, Priority
from src.rate_limiter.rate_limiter import (
    RateLimiter, 
    MultiExchangeRateLimiter,
    ExchangeLimitConfig,
    RateLimiterBackend,
    InMemoryBackend
)


@dataclass
class RateLimitHeaders:
    """Parsed rate limit headers from exchange response.
    
    Different exchanges use different header formats:
    - Binance: X-MBX-USED-WEIGHT, X-MBX-ORDER-COUNT
    - Coinbase: CF-RateLimit-Used, CF-RateLimit-Remaining
    - Kraken: standard rate limit headers
    """
    # Common fields
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset_at: Optional[float] = None  # Unix timestamp
    retry_after: Optional[float] = None  # Seconds to wait
    
    # Weight-based (Binance-style)
    used_weight: Optional[int] = None
    weight_limit: Optional[int] = None
    
    # Order count (Binance-style)
    order_count: Optional[int] = None
    order_limit: Optional[int] = None
    
    # Raw headers for exchange-specific handling
    raw_headers: Dict[str, str] = field(default_factory=dict)
    
    @property
    def is_rate_limited(self) -> bool:
        """Check if rate limit has been hit."""
        return self.remaining == 0 if self.remaining is not None else False
    
    @property
    def utilization(self) -> float:
        """Get current utilization (0.0 to 1.0)."""
        if self.remaining is not None and self.limit:
            return 1.0 - (self.remaining / self.limit)
        if self.used_weight is not None and self.weight_limit:
            return self.used_weight / self.weight_limit
        return 0.0
    
    @classmethod
    def from_binance_headers(cls, headers: Dict[str, str]) -> "RateLimitHeaders":
        """Parse Binance-style headers."""
        result = cls(raw_headers=dict(headers))
        
        # Weight headers
        if "X-MBX-USED-WEIGHT-1M" in headers:
            result.used_weight = int(headers["X-MBX-USED-WEIGHT-1M"])
        elif "X-MBX-USED-WEIGHT-1S" in headers:
            result.used_weight = int(headers["X-MBX-USED-WEIGHT-1S"])
        
        if "X-MBX-ORDER-COUNT-10S" in headers:
            result.order_count = int(headers["X-MBX-ORDER-COUNT-10S"])
        
        # Retry after
        if "Retry-After" in headers:
            result.retry_after = float(headers["Retry-After"])
        
        return result
    
    @classmethod
    def from_coinbase_headers(cls, headers: Dict[str, str]) -> "RateLimitHeaders":
        """Parse Coinbase-style headers."""
        result = cls(raw_headers=dict(headers))
        
        if "CF-RateLimit-Used" in headers:
            result.used_weight = int(headers["CF-RateLimit-Used"])
        if "CF-RateLimit-Remaining" in headers:
            result.remaining = int(headers["CF-RateLimit-Remaining"])
        if "CF-RateLimit-Limit" in headers:
            result.limit = int(headers["CF-RateLimit-Limit"])
        if "CF-RateLimit-Reset" in headers:
            result.reset_at = float(headers["CF-RateLimit-Reset"])
        
        return result
    
    @classmethod
    def from_kraken_headers(cls, headers: Dict[str, str]) -> "RateLimitHeaders":
        """Parse Kraken-style headers."""
        result = cls(raw_headers=dict(headers))
        
        # Kraken uses body for rate limits, headers are minimal
        if "Retry-After" in headers:
            result.retry_after = float(headers["Retry-After"])
        
        return result
    
    @classmethod
    def from_generic_headers(cls, headers: Dict[str, str]) -> "RateLimitHeaders":
        """Parse generic rate limit headers."""
        result = cls(raw_headers=dict(headers))
        
        # Try common header patterns
        patterns = {
            "limit": [r"X-RateLimit-Limit", r"RateLimit-Limit", r"X-Rate-Limit"],
            "remaining": [r"X-RateLimit-Remaining", r"RateLimit-Remaining", r"X-Rate-Remaining"],
            "reset": [r"X-RateLimit-Reset", r"RateLimit-Reset", r"X-Rate-Reset"],
            "retry": [r"Retry-After", r"X-Retry-After"],
        }
        
        for key, value in headers.items():
            key_lower = key.lower()
            
            # Check each pattern
            if any(re.match(p, key, re.I) for p in patterns["limit"]):
                try:
                    result.limit = int(value)
                except ValueError:
                    pass
            elif any(re.match(p, key, re.I) for p in patterns["remaining"]):
                try:
                    result.remaining = int(value)
                except ValueError:
                    pass
            elif any(re.match(p, key, re.I) for p in patterns["reset"]):
                try:
                    # Handle both Unix timestamp and seconds
                    reset_val = float(value)
                    if reset_val > 1_000_000_000:  # Unix timestamp
                        result.reset_at = reset_val
                    else:  # Seconds from now
                        result.reset_at = time.time() + reset_val
                except ValueError:
                    pass
            elif any(re.match(p, key, re.I) for p in patterns["retry"]):
                try:
                    result.retry_after = float(value)
                except ValueError:
                    pass
        
        return result
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {k: v for k, v in asdict(self).items() if k != "raw_headers"}


@dataclass(order=True)
class QueuedRequest:
    """Request in the priority queue."""
    priority_neg: int  # Negated for min-heap
    timestamp: float
    exchange: str = field(compare=False)
    path: str = field(compare=False)
    method: str = field(compare=False)
    weight: float = field(compare=False, default=1.0)
    future: asyncio.Future = field(compare=False)
    timeout: Optional[float] = field(compare=False, default=None)
    
    @property
    def priority(self) -> Priority:
        return Priority(-self.priority_neg)


@dataclass
class ExchangeStatus:
    """Current status of an exchange's rate limits."""
    exchange: str
    last_update: float
    rate_limit_headers: Optional[RateLimitHeaders] = None
    current_utilization: float = 0.0
    is_healthy: bool = True
    consecutive_errors: int = 0
    
    # Adaptive rate adjustment
    adaptive_rate_multiplier: float = 1.0
    last_rate_limit_hit: Optional[float] = None


class GlobalRateCoordinator:
    """Singleton coordinator for global rate limit management.
    
    Manages rate limiting across multiple exchanges with:
    - Priority-based request queuing
    - Cross-exchange rate limit tracking
    - Dynamic rate adjustment based on feedback
    - Rate limit header parsing
    
    This is a singleton - use GlobalRateCoordinator.get_instance() to access.
    
    Example:
        >>> coordinator = GlobalRateCoordinator.get_instance()
        >>> coordinator.register_exchange("binance", binance_config)
        >>> 
        >>> # In request handler
        >>> async with coordinator.request("binance", "/api/v3/order", "POST", priority=Priority.HIGH):
        ...     # Make the request
        ...     response = await http_client.post(...)
        ...     coordinator.record_response("binance", response.headers, response.status_code)
    """
    
    _instance: Optional["GlobalRateCoordinator"] = None
    _lock = asyncio.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_instance(cls) -> "GlobalRateCoordinator":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (mainly for testing)."""
        cls._instance = None
    
    def __init__(
        self,
        backend: Optional[RateLimiterBackend] = None,
        max_concurrent_requests: int = 100,
        enable_priority_queue: bool = True
    ):
        """Initialize the coordinator (only called once due to singleton).
        
        Args:
            backend: Storage backend for distributed coordination
            max_concurrent_requests: Global concurrent request limit
            enable_priority_queue: Enable priority-based request queuing
        """
        # Prevent re-initialization
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        
        self._backend = backend or InMemoryBackend()
        self._max_concurrent = max_concurrent_requests
        self._enable_priority_queue = enable_priority_queue
        
        # Rate limiters per exchange
        self._limiters = MultiExchangeRateLimiter(self._backend)
        
        # Exchange status tracking
        self._exchange_status: Dict[str, ExchangeStatus] = {}
        
        # Global request semaphore for concurrency control
        self._concurrency_semaphore = asyncio.Semaphore(max_concurrent_requests)
        
        # Priority queue for requests
        self._request_queue: List[QueuedRequest] = []
        self._queue_lock = asyncio.Lock()
        self._queue_processor: Optional[asyncio.Task] = None
        
        # Request callbacks
        self._on_rate_limit: Optional[Callable[[str, RateLimitHeaders], None]] = None
        self._on_error: Optional[Callable[[str, Exception], None]] = None
        
        # Health check task
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    def register_exchange(
        self,
        exchange: str,
        config: ExchangeLimitConfig,
        enable_sliding_window: bool = False
    ) -> RateLimiter:
        """Register an exchange with the coordinator.
        
        Args:
            exchange: Exchange name
            config: Rate limit configuration
            enable_sliding_window: Use sliding window algorithm
            
        Returns:
            The rate limiter for this exchange
        """
        limiter = self._limiters.add_exchange(config, enable_sliding_window)
        
        # Initialize exchange status
        self._exchange_status[exchange] = ExchangeStatus(
            exchange=exchange,
            last_update=time.time(),
            is_healthy=True
        )
        
        return limiter
    
    def get_exchange_config(self, exchange: str) -> Optional[ExchangeLimitConfig]:
        """Get configuration for an exchange."""
        limiter = self._limiters.get_limiter(exchange)
        return limiter._config if limiter else None
    
    def update_exchange_config(self, exchange: str, config: ExchangeLimitConfig) -> None:
        """Update configuration for an exchange."""
        limiter = self._limiters.get_limiter(exchange)
        if limiter:
            asyncio.create_task(limiter.update_config(config))
    
    async def acquire(
        self,
        exchange: str,
        path: str,
        method: str = "GET",
        weight: float = 1.0,
        priority: Priority = Priority.NORMAL,
        timeout: Optional[float] = None,
        wait: bool = True
    ) -> bool:
        """Acquire permission to make a request.
        
        Args:
            exchange: Exchange name
            path: API endpoint path
            method: HTTP method
            weight: Request weight
            priority: Request priority
            timeout: Maximum wait time
            wait: If False, return immediately if can't acquire
            
        Returns:
            True if request can proceed
        """
        if self._shutdown:
            raise RuntimeError("Coordinator is shut down")
        
        # Check exchange health
        status = self._exchange_status.get(exchange)
        if status and not status.is_healthy:
            # Still allow but at lower priority
            priority = Priority(max(Priority.LOW.value, priority.value - 1))
        
        # Use priority queue if enabled
        if self._enable_priority_queue and priority.value >= Priority.HIGH.value:
            return await self._queue_high_priority_request(
                exchange, path, method, weight, priority, timeout
            )
        
        # Direct acquisition
        return await self._do_acquire(exchange, path, method, weight, priority, timeout, wait)
    
    async def _do_acquire(
        self,
        exchange: str,
        path: str,
        method: str,
        weight: float,
        priority: Priority,
        timeout: Optional[float],
        wait: bool
    ) -> bool:
        """Internal acquisition method."""
        try:
            # Acquire concurrency slot
            if wait:
                await asyncio.wait_for(
                    self._concurrency_semaphore.acquire(),
                    timeout=timeout
                )
            else:
                if self._concurrency_semaphore.locked():
                    return False
                self._concurrency_semaphore.acquire_nowait()
            
            # Acquire rate limit
            acquired = await self._limiters.acquire(
                exchange, path, method,
                weight=weight,
                priority=priority,
                timeout=timeout
            )
            
            if not acquired:
                self._concurrency_semaphore.release()
                return False
            
            return True
            
        except asyncio.TimeoutError:
            return False
    
    async def _queue_high_priority_request(
        self,
        exchange: str,
        path: str,
        method: str,
        weight: float,
        priority: Priority,
        timeout: Optional[float]
    ) -> bool:
        """Queue a high-priority request."""
        future = asyncio.get_event_loop().create_future()
        
        request = QueuedRequest(
            priority_neg=-int(priority),
            timestamp=time.time(),
            exchange=exchange,
            path=path,
            method=method,
            weight=weight,
            future=future,
            timeout=timeout
        )
        
        async with self._queue_lock:
            heapq.heappush(self._request_queue, request)
            
            # Start queue processor if not running
            if self._queue_processor is None or self._queue_processor.done():
                self._queue_processor = asyncio.create_task(self._process_queue())
        
        try:
            if timeout:
                await asyncio.wait_for(future, timeout=timeout)
            else:
                await future
            return True
        except asyncio.TimeoutError:
            # Remove from queue
            async with self._queue_lock:
                if request in self._request_queue:
                    self._request_queue.remove(request)
                    heapq.heapify(self._request_queue)
            return False
    
    async def _process_queue(self) -> None:
        """Process queued high-priority requests."""
        while not self._shutdown:
            async with self._queue_lock:
                if not self._request_queue:
                    break
                
                request = self._request_queue[0]  # Peek at highest priority
                
                # Try to acquire
                if await self._do_acquire(
                    request.exchange,
                    request.path,
                    request.method,
                    request.weight,
                    request.priority,
                    timeout=0,  # Don't wait in processor
                    wait=False
                ):
                    heapq.heappop(self._request_queue)
                    if not request.future.done():
                        request.future.set_result(True)
                else:
                    # Can't acquire yet, wait a bit
                    break
            
            await asyncio.sleep(0.01)  # Small delay to prevent busy-waiting
    
    def release(self, exchange: str, path: str, method: str = "GET") -> None:
        """Release resources after request completion."""
        self._limiters.release(exchange, path, method)
        try:
            self._concurrency_semaphore.release()
        except ValueError:
            # Semaphore was over-released
            pass
    
    async def request(
        self,
        exchange: str,
        path: str,
        method: str = "GET",
        weight: float = 1.0,
        priority: Priority = Priority.NORMAL,
        timeout: Optional[float] = None
    ):
        """Context manager for making rate-limited requests.
        
        Example:
            >>> async with coordinator.request("binance", "/api/v3/order", "POST"):
            ...     response = await client.post(url)
        """
        acquired = await self.acquire(exchange, path, method, weight, priority, timeout)
        if not acquired:
            raise RateLimitExceeded(f"Could not acquire rate limit for {exchange}")
        
        class RateLimitContext:
            def __init__(ctx_self, coord, exc, p, m):
                ctx_self.coord = coord
                ctx_self.exchange = exc
                ctx_self.path = p
                ctx_self.method = m
            
            async def __aenter__(ctx_self):
                return ctx_self
            
            async def __aexit__(ctx_self, exc_type, exc_val, exc_tb):
                ctx_self.coord.release(ctx_self.exchange, ctx_self.path, ctx_self.method)
        
        return RateLimitContext(self, exchange, path, method)
    
    def record_response(
        self,
        exchange: str,
        headers: Dict[str, str],
        status_code: int,
        exchange_type: str = "generic"
    ) -> RateLimitHeaders:
        """Record response headers for rate limit tracking.
        
        Args:
            exchange: Exchange name
            headers: Response headers
            status_code: HTTP status code
            exchange_type: Type of exchange for header parsing
            
        Returns:
            Parsed rate limit headers
        """
        # Parse headers based on exchange type
        parsers = {
            "binance": RateLimitHeaders.from_binance_headers,
            "coinbase": RateLimitHeaders.from_coinbase_headers,
            "kraken": RateLimitHeaders.from_kraken_headers,
            "generic": RateLimitHeaders.from_generic_headers,
        }
        
        parser = parsers.get(exchange_type, RateLimitHeaders.from_generic_headers)
        parsed = parser(headers)
        
        # Update exchange status
        status = self._exchange_status.get(exchange)
        if status:
            status.last_update = time.time()
            status.rate_limit_headers = parsed
            status.current_utilization = parsed.utilization
            
            # Handle rate limit hit
            if status_code == 429 or parsed.is_rate_limited:
                status.last_rate_limit_hit = time.time()
                status.consecutive_errors += 1
                
                # Adjust adaptive rate
                self._adjust_rate_for_exchange(exchange, parsed)
                
                # Notify callback
                if self._on_rate_limit:
                    self._on_rate_limit(exchange, parsed)
            else:
                status.consecutive_errors = 0
                status.is_healthy = True
                
                # Record success
                limiter = self._limiters.get_limiter(exchange)
                if limiter:
                    asyncio.create_task(limiter.record_success("/", 0.1))
        
        return parsed
    
    def _adjust_rate_for_exchange(
        self,
        exchange: str,
        headers: RateLimitHeaders
    ) -> None:
        """Adjust rate limits based on response."""
        status = self._exchange_status.get(exchange)
        if not status:
            return
        
        # Reduce adaptive multiplier
        status.adaptive_rate_multiplier *= 0.8
        
        # Update limiter with retry_after info
        limiter = self._limiters.get_limiter(exchange)
        if limiter:
            asyncio.create_task(
                limiter.record_rate_limit_hit(headers.retry_after)
            )
    
    def record_error(self, exchange: str, error: Exception) -> None:
        """Record an error for health tracking."""
        status = self._exchange_status.get(exchange)
        if status:
            status.consecutive_errors += 1
            status.last_update = time.time()
            
            # Mark unhealthy after consecutive errors
            if status.consecutive_errors >= 5:
                status.is_healthy = False
        
        if self._on_error:
            self._on_error(exchange, error)
    
    def set_rate_limit_callback(
        self,
        callback: Callable[[str, RateLimitHeaders], None]
    ) -> None:
        """Set callback for rate limit events."""
        self._on_rate_limit = callback
    
    def set_error_callback(
        self,
        callback: Callable[[str, Exception], None]
    ) -> None:
        """Set callback for error events."""
        self._on_error = callback
    
    @property
    def metrics(self) -> Dict[str, Any]:
        """Get coordinator metrics."""
        return {
            "exchanges": list(self._exchange_status.keys()),
            "exchange_status": {
                name: {
                    "healthy": status.is_healthy,
                    "utilization": status.current_utilization,
                    "consecutive_errors": status.consecutive_errors,
                    "adaptive_multiplier": status.adaptive_rate_multiplier,
                }
                for name, status in self._exchange_status.items()
            },
            "rate_limiters": self._limiters.get_all_metrics(),
            "queue_size": len(self._request_queue),
            "concurrent_requests": (
                self._max_concurrent - self._concurrency_semaphore._value
            ),
        }
    
    def get_exchange_status(self, exchange: str) -> Optional[ExchangeStatus]:
        """Get current status for an exchange."""
        return self._exchange_status.get(exchange)
    
    async def _health_check_loop(self) -> None:
        """Periodic health check task."""
        while not self._shutdown:
            try:
                now = time.time()
                
                for exchange, status in self._exchange_status.items():
                    # Reset healthy status after cooldown
                    if not status.is_healthy:
                        if status.last_rate_limit_hit:
                            cooldown = 60.0  # 1 minute cooldown
                            if now - status.last_rate_limit_hit > cooldown:
                                status.is_healthy = True
                                status.consecutive_errors = 0
                                
                                # Gradually restore rate
                                if status.adaptive_rate_multiplier < 1.0:
                                    status.adaptive_rate_multiplier = min(
                                        1.0,
                                        status.adaptive_rate_multiplier * 1.1
                                    )
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(30)
    
    async def start(self) -> None:
        """Start the coordinator background tasks."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def shutdown(self) -> None:
        """Shutdown the coordinator."""
        self._shutdown = True
        
        # Cancel health check
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Cancel queue processor
        if self._queue_processor and not self._queue_processor.done():
            self._queue_processor.cancel()
            try:
                await self._queue_processor
            except asyncio.CancelledError:
                pass
        
        # Cancel queued requests
        async with self._queue_lock:
            for request in self._request_queue:
                if not request.future.done():
                    request.future.set_exception(asyncio.CancelledError("Coordinator shutdown"))
            self._request_queue = []
        
        # Shutdown limiters
        await self._limiters.shutdown_all()


class RateLimitExceeded(Exception):
    """Exception raised when rate limit cannot be acquired."""
    pass


# Convenience function for getting coordinator
def get_coordinator() -> GlobalRateCoordinator:
    """Get the global rate coordinator instance."""
    return GlobalRateCoordinator.get_instance()
