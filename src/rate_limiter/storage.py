"""Storage backends for rate limit state persistence.

Provides:
- Redis backend for distributed rate limiting
- In-memory backend for single-instance
- Rate limit state persistence across restarts
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, asdict
from collections import defaultdict
import pickle
import hashlib
import os

from src.rate_limiter.rate_limiter import RateLimiterBackend


@dataclass
class RateLimitState:
    """Serializable rate limit state.

    Used for persistence across restarts.
    """

    exchange: str
    bucket_name: str
    tokens: float
    last_update: float
    rate: float
    capacity: float
    acquired_count: int = 0
    rejected_count: int = 0


class StorageBackend(ABC):
    """Abstract base for storage backends."""

    @abstractmethod
    async def save_state(self, key: str, state: Dict[str, Any]) -> None:
        """Save state to storage."""
        pass

    @abstractmethod
    async def load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Load state from storage."""
        pass

    @abstractmethod
    async def delete_state(self, key: str) -> None:
        """Delete state from storage."""
        pass

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> List[str]:
        """List all keys with given prefix."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        pass


class FileStorageBackend(StorageBackend):
    """File-based storage for rate limit state.

    Stores state in JSON files on disk.
    Good for single-instance deployments.

    Example:
        >>> storage = FileStorageBackend("/var/lib/ratelimit")
        >>> await storage.save_state("binance_global", state_dict)
    """

    def __init__(self, base_path: str):
        """Initialize file storage.

        Args:
            base_path: Directory to store state files
        """
        self._base_path = os.path.expanduser(base_path)
        os.makedirs(self._base_path, exist_ok=True)
        self._lock = asyncio.Lock()

    def _get_file_path(self, key: str) -> str:
        """Get file path for a key."""
        # Sanitize key for filesystem
        safe_key = hashlib.md5(key.encode()).hexdigest()
        return os.path.join(self._base_path, f"{safe_key}.json")

    async def save_state(self, key: str, state: Dict[str, Any]) -> None:
        """Save state to file."""
        async with self._lock:
            file_path = self._get_file_path(key)
            temp_path = file_path + ".tmp"

            # Write to temp file first (atomic write)
            with open(temp_path, "w") as f:
                json.dump(state, f, default=str)

            # Atomic rename
            os.replace(temp_path, file_path)

    async def load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Load state from file."""
        file_path = self._get_file_path(key)

        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    async def delete_state(self, key: str) -> None:
        """Delete state file."""
        file_path = self._get_file_path(key)
        if os.path.exists(file_path):
            os.remove(file_path)

    async def list_keys(self, prefix: str = "") -> List[str]:
        """List all stored keys."""
        keys = []
        for filename in os.listdir(self._base_path):
            if filename.endswith(".json"):
                # We can't reverse the hash, so return hashes
                keys.append(filename[:-5])  # Remove .json
        return keys

    async def exists(self, key: str) -> bool:
        """Check if state exists."""
        return os.path.exists(self._get_file_path(key))

    async def clear_all(self) -> None:
        """Clear all stored state."""
        async with self._lock:
            for filename in os.listdir(self._base_path):
                if filename.endswith(".json"):
                    os.remove(os.path.join(self._base_path, filename))


class RedisStorageBackend(StorageBackend, RateLimiterBackend):
    """Redis-based storage for distributed rate limiting.

    Supports both state persistence and rate limit operations.
    Good for multi-instance deployments.

    Example:
        >>> storage = RedisStorageBackend("redis://localhost:6379")
        >>> await storage.save_state("binance_global", state_dict)
        >>> await storage.increment("request_count", 1, ttl=60)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        key_prefix: str = "ratelimit:",
        default_ttl: int = 3600,
    ):
        """Initialize Redis storage.

        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for all keys
            default_ttl: Default TTL for keys in seconds
        """
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._default_ttl = default_ttl
        self._redis = None
        self._lock = asyncio.Lock()

    def _get_key(self, key: str) -> str:
        """Get prefixed key."""
        return f"{self._key_prefix}{key}"

    async def _get_redis(self):
        """Get or create Redis connection."""
        if self._redis is None:
            try:
                import aioredis

                self._redis = await aioredis.from_url(self._redis_url)
            except ImportError:
                raise ImportError(
                    "aioredis is required for RedisStorageBackend. "
                    "Install with: pip install aioredis"
                )
        return self._redis

    async def save_state(self, key: str, state: Dict[str, Any]) -> None:
        """Save state to Redis."""
        redis = await self._get_redis()
        full_key = self._get_key(f"state:{key}")

        async with self._lock:
            await redis.setex(full_key, self._default_ttl, json.dumps(state, default=str))

    async def load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Load state from Redis."""
        redis = await self._get_redis()
        full_key = self._get_key(f"state:{key}")

        data = await redis.get(full_key)
        if data:
            return json.loads(data)
        return None

    async def delete_state(self, key: str) -> None:
        """Delete state from Redis."""
        redis = await self._get_redis()
        full_key = self._get_key(f"state:{key}")
        await redis.delete(full_key)

    async def list_keys(self, prefix: str = "") -> List[str]:
        """List all keys with given prefix."""
        redis = await self._get_redis()
        pattern = self._get_key(f"state:{prefix}*")

        keys = []
        async for key in redis.scan_iter(match=pattern):
            # Remove prefix and 'state:'
            key_str = key.decode() if isinstance(key, bytes) else key
            keys.append(key_str.replace(self._get_key("state:"), ""))
        return keys

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        redis = await self._get_redis()
        full_key = self._get_key(f"state:{key}")
        return await redis.exists(full_key) > 0

    # RateLimiterBackend implementation

    async def get_counter(self, key: str) -> float:
        """Get counter value."""
        redis = await self._get_redis()
        full_key = self._get_key(f"counter:{key}")

        value = await redis.get(full_key)
        return float(value) if value else 0.0

    async def increment(self, key: str, amount: float = 1.0, ttl: Optional[float] = None) -> float:
        """Increment counter."""
        redis = await self._get_redis()
        full_key = self._get_key(f"counter:{key}")

        # Use INCR for integer amounts, otherwise use GET/SET
        if amount == 1.0:
            pipe = redis.pipeline()
            pipe.incr(full_key)
            if ttl:
                pipe.expire(full_key, int(ttl))
            results = await pipe.execute()
            return float(results[0])
        else:
            # Float increment
            current = float(await redis.get(full_key) or 0)
            new_value = current + amount
            await redis.setex(full_key, int(ttl or self._default_ttl), str(new_value))
            return new_value

    async def set_counter(self, key: str, value: float, ttl: Optional[float] = None) -> None:
        """Set counter value."""
        redis = await self._get_redis()
        full_key = self._get_key(f"counter:{key}")

        await redis.setex(full_key, int(ttl or self._default_ttl), str(value))

    async def get_window_requests(self, key: str, window_start: float) -> List[float]:
        """Get request timestamps within sliding window."""
        redis = await self._get_redis()
        full_key = self._get_key(f"window:{key}")

        # Use sorted set for efficient range queries
        min_score = window_start
        max_score = time.time()

        timestamps = await redis.zrangebyscore(full_key, min_score, max_score, withscores=False)

        return [float(ts) for ts in timestamps]

    async def add_window_request(self, key: str, timestamp: float, ttl: float) -> None:
        """Add request timestamp to sliding window."""
        redis = await self._get_redis()
        full_key = self._get_key(f"window:{key}")

        # Add to sorted set with timestamp as score
        await redis.zadd(full_key, {str(timestamp): timestamp})

        # Set expiration on the key
        await redis.expire(full_key, int(ttl))

        # Clean old entries (optional optimization)
        await redis.zremrangebyscore(full_key, 0, timestamp - ttl)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None


class HybridStorageBackend(StorageBackend, RateLimiterBackend):
    """Hybrid storage using in-memory with Redis fallback.

    Uses in-memory storage for speed, Redis for persistence and coordination.

    Example:
        >>> storage = HybridStorageBackend(
        ...     redis_url="redis://localhost:6379",
        ...     sync_interval=5.0
        ... )
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "ratelimit:",
        sync_interval: float = 5.0,
        default_ttl: int = 3600,
    ):
        """Initialize hybrid storage.

        Args:
            redis_url: Redis URL (None for memory-only)
            key_prefix: Key prefix
            sync_interval: How often to sync to Redis (seconds)
            default_ttl: Default TTL
        """
        self._memory = InMemoryBackend()
        self._redis: Optional[RedisStorageBackend] = None
        self._sync_interval = sync_interval
        self._key_prefix = key_prefix

        if redis_url:
            self._redis = RedisStorageBackend(redis_url, key_prefix, default_ttl)

        self._sync_task: Optional[asyncio.Task] = None
        self._dirty_keys: Set[str] = set()
        self._lock = asyncio.Lock()
        self._shutdown = False

    async def start(self) -> None:
        """Start background sync task."""
        if self._redis and (self._sync_task is None or self._sync_task.done()):
            self._sync_task = asyncio.create_task(self._sync_loop())

    async def _sync_loop(self) -> None:
        """Periodically sync dirty keys to Redis."""
        while not self._shutdown:
            try:
                await self._sync_to_redis()
                await asyncio.sleep(self._sync_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(self._sync_interval)

    async def _sync_to_redis(self) -> None:
        """Sync dirty keys to Redis."""
        if not self._redis or not self._dirty_keys:
            return

        async with self._lock:
            keys_to_sync = list(self._dirty_keys)
            self._dirty_keys.clear()

        for key in keys_to_sync:
            try:
                value = await self._memory.get_counter(key)
                await self._redis.set_counter(key, value)
            except Exception:
                # Mark as dirty again on failure
                self._dirty_keys.add(key)

    async def save_state(self, key: str, state: Dict[str, Any]) -> None:
        """Save state."""
        await self._memory.set_counter(f"state:{key}", 1)  # Mark as existing

        if self._redis:
            await self._redis.save_state(key, state)

    async def load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Load state from Redis."""
        if self._redis:
            return await self._redis.load_state(key)
        return None

    async def delete_state(self, key: str) -> None:
        """Delete state."""
        if self._redis:
            await self._redis.delete_state(key)

    async def list_keys(self, prefix: str = "") -> List[str]:
        """List keys."""
        if self._redis:
            return await self._redis.list_keys(prefix)
        return []

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        memory_exists = await self._memory.get_counter(f"state:{key}") > 0

        if memory_exists:
            return True

        if self._redis:
            return await self._redis.exists(key)

        return False

    # RateLimiterBackend implementation (delegates to memory)

    async def get_counter(self, key: str) -> float:
        return await self._memory.get_counter(key)

    async def increment(self, key: str, amount: float = 1.0, ttl: Optional[float] = None) -> float:
        result = await self._memory.increment(key, amount, ttl)
        async with self._lock:
            self._dirty_keys.add(key)
        return result

    async def set_counter(self, key: str, value: float, ttl: Optional[float] = None) -> None:
        await self._memory.set_counter(key, value, ttl)
        async with self._lock:
            self._dirty_keys.add(key)

    async def get_window_requests(self, key: str, window_start: float) -> List[float]:
        return await self._memory.get_window_requests(key, window_start)

    async def add_window_request(self, key: str, timestamp: float, ttl: float) -> None:
        await self._memory.add_window_request(key, timestamp, ttl)
        async with self._lock:
            self._dirty_keys.add(key)

    async def shutdown(self) -> None:
        """Shutdown and cleanup."""
        self._shutdown = True

        # Final sync
        if self._redis:
            await self._sync_to_redis()

        # Cancel sync task
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        # Close Redis
        if self._redis:
            await self._redis.close()


class RateLimitStateManager:
    """Manages persistence of rate limit state.

    Handles saving and restoring rate limit state across restarts.

    Example:
        >>> manager = RateLimitStateManager(FileStorageBackend("/var/lib/ratelimit"))
        >>> await manager.save_token_bucket_state("binance", bucket)
        >>> bucket = await manager.restore_token_bucket_state("binance", "global")
    """

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    async def save_token_bucket_state(self, exchange: str, bucket_name: str, bucket) -> None:
        """Save token bucket state."""
        state = RateLimitState(
            exchange=exchange,
            bucket_name=bucket_name,
            tokens=bucket.tokens,
            last_update=time.time(),
            rate=bucket.rate,
            capacity=bucket.capacity,
            acquired_count=bucket._acquire_count,
            rejected_count=bucket._rejected_count,
        )

        key = f"{exchange}:{bucket_name}"
        await self._storage.save_state(key, asdict(state))

    async def restore_token_bucket_state(
        self, exchange: str, bucket_name: str, bucket_class=None
    ) -> Optional[Any]:
        """Restore token bucket from saved state."""
        from src.rate_limiter.token_bucket import TokenBucket

        key = f"{exchange}:{bucket_name}"
        data = await self._storage.load_state(key)

        if not data:
            return None

        # Calculate elapsed time and refill tokens
        elapsed = time.time() - data.get("last_update", time.time())
        tokens = min(
            data.get("tokens", 0) + elapsed * data.get("rate", 10), data.get("capacity", 20)
        )

        # Create bucket
        bucket = (bucket_class or TokenBucket)(
            rate=data.get("rate", 10.0),
            capacity=data.get("capacity", 20.0),
            initial_tokens=tokens,
            name=bucket_name,
        )

        # Restore metrics
        bucket._acquire_count = data.get("acquired_count", 0)
        bucket._rejected_count = data.get("rejected_count", 0)

        return bucket

    async def save_all_states(self, limiters: Dict[str, Any]) -> None:
        """Save all rate limiter states."""
        for exchange, limiter in limiters.items():
            # Save global bucket
            if hasattr(limiter, "_global_bucket"):
                await self.save_token_bucket_state(exchange, "global", limiter._global_bucket)

            # Save order bucket
            if hasattr(limiter, "_order_bucket"):
                await self.save_token_bucket_state(exchange, "order", limiter._order_bucket)

            # Save endpoint buckets
            if hasattr(limiter, "_endpoint_buckets"):
                for name, bucket in limiter._endpoint_buckets.items():
                    await self.save_token_bucket_state(exchange, name, bucket)

    async def list_saved_exchanges(self) -> List[str]:
        """List all exchanges with saved state."""
        keys = await self._storage.list_keys()
        exchanges = set()
        for key in keys:
            if ":" in key:
                exchanges.add(key.split(":")[0])
        return list(exchanges)

    async def clear_all_states(self) -> None:
        """Clear all saved states."""
        keys = await self._storage.list_keys()
        for key in keys:
            await self._storage.delete_state(key)


def create_storage_backend(backend_type: str = "memory", **kwargs) -> StorageBackend:
    """Factory function to create storage backends.

    Args:
        backend_type: "memory", "file", "redis", or "hybrid"
        **kwargs: Backend-specific arguments

    Returns:
        Configured storage backend

    Example:
        >>> storage = create_storage_backend("redis", redis_url="redis://localhost")
        >>> storage = create_storage_backend("file", base_path="/var/lib/ratelimit")
    """
    if backend_type == "memory":
        return InMemoryBackend()

    elif backend_type == "file":
        return FileStorageBackend(kwargs.get("base_path", "~/.ratelimit"))

    elif backend_type == "redis":
        return RedisStorageBackend(
            redis_url=kwargs.get("redis_url", "redis://localhost:6379"),
            key_prefix=kwargs.get("key_prefix", "ratelimit:"),
            default_ttl=kwargs.get("default_ttl", 3600),
        )

    elif backend_type == "hybrid":
        return HybridStorageBackend(
            redis_url=kwargs.get("redis_url"),
            key_prefix=kwargs.get("key_prefix", "ratelimit:"),
            sync_interval=kwargs.get("sync_interval", 5.0),
            default_ttl=kwargs.get("default_ttl", 3600),
        )

    else:
        raise ValueError(f"Unknown backend type: {backend_type}")
