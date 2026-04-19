"""Metrics collection module for algo-trading system.

This module provides async-safe metrics collection with support for:
- Counters, Gauges, and Histograms
- Request latency tracking (p50, p95, p99)
- Request throughput
- Error rates by status code and exception type
- Active connection gauges
- Exchange-specific metrics (order placement latency, fill rates)
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Set, Union, Type
import logging
import structlog
from contextlib import asynccontextmanager

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


logger = structlog.get_logger(__name__)


class MetricType(str, Enum):
    """Metric type enumeration."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    INFO = "info"


@dataclass
class HistogramSnapshot:
    """Snapshot of histogram data."""

    count: int
    sum: float
    buckets: Dict[float, int]
    p50: float
    p95: float
    p99: float
    min: float
    max: float


@dataclass
class MetricValue:
    """Metric value container."""

    name: str
    value: Union[int, float, str, Dict[str, Any]]
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: MetricType = MetricType.GAUGE


class AsyncCounter:
    """Thread-safe and async-safe counter."""

    def __init__(self, name: str, description: str = "", labels: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self._lock = asyncio.Lock()
        self._values: Dict[str, int] = defaultdict(int)
        self._label_names = labels or []

    async def inc(self, value: int = 1, **labels) -> None:
        """Increment counter by value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            self._values[label_key] += value

    async def get(self, **labels) -> int:
        """Get current counter value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            return self._values[label_key]

    async def get_all(self) -> Dict[str, int]:
        """Get all counter values."""
        async with self._lock:
            return dict(self._values)

    def _make_label_key(self, **labels) -> str:
        """Create label key from label values."""
        if not self._label_names:
            return "_default"
        parts = []
        for name in self._label_names:
            parts.append(f"{name}={labels.get(name, '')}")
        return "|".join(parts) if parts else "_default"


class AsyncGauge:
    """Thread-safe and async-safe gauge."""

    def __init__(self, name: str, description: str = "", labels: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self._lock = asyncio.Lock()
        self._values: Dict[str, float] = defaultdict(float)
        self._label_names = labels or []

    async def set(self, value: float, **labels) -> None:
        """Set gauge to value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            self._values[label_key] = value

    async def inc(self, value: float = 1.0, **labels) -> None:
        """Increment gauge by value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            self._values[label_key] += value

    async def dec(self, value: float = 1.0, **labels) -> None:
        """Decrement gauge by value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            self._values[label_key] -= value

    async def get(self, **labels) -> float:
        """Get current gauge value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            return self._values[label_key]

    async def get_all(self) -> Dict[str, float]:
        """Get all gauge values."""
        async with self._lock:
            return dict(self._values)

    def _make_label_key(self, **labels) -> str:
        """Create label key from label values."""
        if not self._label_names:
            return "_default"
        parts = []
        for name in self._label_names:
            parts.append(f"{name}={labels.get(name, '')}")
        return "|".join(parts) if parts else "_default"


class AsyncHistogram:
    """Thread-safe and async-safe histogram with percentile calculation."""

    DEFAULT_BUCKETS = [
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
        60.0,
        300.0,
        600.0,
    ]

    def __init__(
        self,
        name: str,
        description: str = "",
        buckets: Optional[List[float]] = None,
        labels: Optional[List[str]] = None,
    ):
        self.name = name
        self.description = description
        self._buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._label_names = labels or []
        self._lock = asyncio.Lock()
        self._values: Dict[str, List[float]] = defaultdict(list)

    async def observe(self, value: float, **labels) -> None:
        """Observe a value."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            self._values[label_key].append(value)
            # Limit storage to prevent memory issues
            if len(self._values[label_key]) > 10000:
                self._values[label_key] = self._values[label_key][-5000:]

    async def get_snapshot(self, **labels) -> Optional[HistogramSnapshot]:
        """Get histogram snapshot with percentiles."""
        label_key = self._make_label_key(**labels)
        async with self._lock:
            values = self._values[label_key].copy()

        if not values:
            return None

        sorted_values = sorted(values)
        count = len(sorted_values)
        total_sum = sum(sorted_values)

        # Calculate buckets
        buckets = {}
        bucket_idx = 0
        for bucket_bound in self._buckets:
            while bucket_idx < count and sorted_values[bucket_idx] <= bucket_bound:
                bucket_idx += 1
            buckets[bucket_bound] = bucket_idx

        # Calculate percentiles
        def percentile(p: float) -> float:
            if count == 0:
                return 0.0
            k = (count - 1) * p / 100.0
            f = int(k)
            c = f + 1 if f + 1 < count else f
            if f == c:
                return sorted_values[f]
            return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)

        return HistogramSnapshot(
            count=count,
            sum=total_sum,
            buckets=buckets,
            p50=percentile(50),
            p95=percentile(95),
            p99=percentile(99),
            min=sorted_values[0],
            max=sorted_values[-1],
        )

    async def get_all_snapshots(self) -> Dict[str, Optional[HistogramSnapshot]]:
        """Get snapshots for all label combinations."""
        async with self._lock:
            keys = list(self._values.keys())

        result = {}
        for key in keys:
            result[key] = await self.get_snapshot_from_key(key)
        return result

    async def get_snapshot_from_key(self, label_key: str) -> Optional[HistogramSnapshot]:
        """Get snapshot from label key."""
        async with self._lock:
            values = self._values[label_key].copy()

        if not values:
            return None

        sorted_values = sorted(values)
        count = len(sorted_values)
        total_sum = sum(sorted_values)

        buckets = {}
        bucket_idx = 0
        for bucket_bound in self._buckets:
            while bucket_idx < count and sorted_values[bucket_idx] <= bucket_bound:
                bucket_idx += 1
            buckets[bucket_bound] = bucket_idx

        def percentile(p: float) -> float:
            if count == 0:
                return 0.0
            k = (count - 1) * p / 100.0
            f = int(k)
            c = f + 1 if f + 1 < count else f
            if f == c:
                return sorted_values[f]
            return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)

        return HistogramSnapshot(
            count=count,
            sum=total_sum,
            buckets=buckets,
            p50=percentile(50),
            p95=percentile(95),
            p99=percentile(99),
            min=sorted_values[0],
            max=sorted_values[-1],
        )

    def _make_label_key(self, **labels) -> str:
        """Create label key from label values."""
        if not self._label_names:
            return "_default"
        parts = []
        for name in self._label_names:
            parts.append(f"{name}={labels.get(name, '')}")
        return "|".join(parts) if parts else "_default"


class RequestTracker:
    """Track HTTP request metrics."""

    def __init__(self, collector: "MetricsCollector"):
        self._collector = collector

    @asynccontextmanager
    async def track_request(self, exchange: str, method: str, endpoint: str):
        """Context manager to track request metrics."""
        start_time = time.time()
        status_code = None
        error_type = None

        try:
            await self._collector.record_request_started(exchange, method)
            yield self
            status_code = 200  # Assume success if no exception
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            error_type = type(e).__name__
            raise
        finally:
            duration = time.time() - start_time
            await self._collector.record_request_completed(
                exchange, method, endpoint, duration, status_code, error_type
            )


class OrderTracker:
    """Track order placement metrics."""

    def __init__(self, collector: "MetricsCollector"):
        self._collector = collector

    @asynccontextmanager
    async def track_order_placement(self, exchange: str, symbol: str, order_type: str):
        """Context manager to track order placement metrics."""
        start_time = time.time()
        success = False

        try:
            yield self
            success = True
        finally:
            duration = time.time() - start_time
            await self._collector.record_order_placement(
                exchange, symbol, order_type, duration, success
            )


class MetricsCollector:
    """Main metrics collector for the trading system.

    Provides async-safe collection of:
    - HTTP request metrics (latency, throughput, errors)
    - Exchange-specific metrics (order latency, fill rates)
    - Connection metrics
    - Trading metrics
    """

    def __init__(self, namespace: str = "algo_trading"):
        """Initialize metrics collector.

        Args:
            namespace: Prefix for all metrics
        """
        self.namespace = namespace
        self._lock = asyncio.Lock()
        self._started_at = time.time()

        # Create registry
        self.registry = CollectorRegistry()

        # HTTP Request metrics
        self.request_count = AsyncCounter(
            f"{namespace}_http_requests_total",
            "Total HTTP requests",
            ["exchange", "method", "endpoint", "status_code"],
        )
        self.request_latency = AsyncHistogram(
            f"{namespace}_http_request_duration_seconds",
            "HTTP request latency in seconds",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            labels=["exchange", "method", "endpoint"],
        )
        self.request_errors = AsyncCounter(
            f"{namespace}_http_request_errors_total",
            "Total HTTP request errors",
            ["exchange", "error_type", "status_code"],
        )
        self.active_requests = AsyncGauge(
            f"{namespace}_http_active_requests", "Number of active HTTP requests", ["exchange"]
        )

        # Connection metrics
        self.active_connections = AsyncGauge(
            f"{namespace}_active_connections",
            "Number of active connections",
            ["exchange", "connection_type"],
        )
        self.connection_attempts = AsyncCounter(
            f"{namespace}_connection_attempts_total",
            "Total connection attempts",
            ["exchange", "status"],
        )
        self.reconnection_count = AsyncCounter(
            f"{namespace}_reconnections_total", "Total reconnections", ["exchange"]
        )

        # Exchange-specific trading metrics
        self.order_placement_latency = AsyncHistogram(
            f"{namespace}_order_placement_duration_seconds",
            "Order placement latency in seconds",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
            labels=["exchange", "symbol", "order_type"],
        )
        self.orders_placed = AsyncCounter(
            f"{namespace}_orders_placed_total",
            "Total orders placed",
            ["exchange", "symbol", "order_type", "status"],
        )
        self.orders_filled = AsyncCounter(
            f"{namespace}_orders_filled_total",
            "Total orders filled",
            ["exchange", "symbol", "fill_type"],
        )
        self.fill_rate = AsyncGauge(
            f"{namespace}_fill_rate", "Order fill rate (0-1)", ["exchange", "symbol"]
        )
        self.order_cancel_count = AsyncCounter(
            f"{namespace}_orders_cancelled_total", "Total orders cancelled", ["exchange", "symbol"]
        )

        # Rate limit metrics
        self.rate_limit_hits = AsyncCounter(
            f"{namespace}_rate_limit_hits_total", "Total rate limit hits", ["exchange"]
        )
        self.rate_limit_wait_time = AsyncHistogram(
            f"{namespace}_rate_limit_wait_seconds",
            "Time spent waiting for rate limits",
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
            labels=["exchange"],
        )

        # WebSocket metrics
        self.ws_messages_received = AsyncCounter(
            f"{namespace}_ws_messages_received_total",
            "Total WebSocket messages received",
            ["exchange", "channel"],
        )
        self.ws_messages_sent = AsyncCounter(
            f"{namespace}_ws_messages_sent_total", "Total WebSocket messages sent", ["exchange"]
        )
        self.ws_errors = AsyncCounter(
            f"{namespace}_ws_errors_total", "Total WebSocket errors", ["exchange", "error_type"]
        )

        # Trading metrics
        self.trade_volume = AsyncCounter(
            f"{namespace}_trade_volume", "Trading volume", ["exchange", "symbol", "side"]
        )
        self.trade_count = AsyncCounter(
            f"{namespace}_trades_total", "Total trades executed", ["exchange", "symbol", "side"]
        )
        self.position_size = AsyncGauge(
            f"{namespace}_position_size", "Current position size", ["exchange", "symbol"]
        )
        self.pnl = AsyncGauge(f"{namespace}_pnl", "Profit and loss", ["exchange", "symbol"])

        # System metrics
        self.uptime_seconds = AsyncGauge(f"{namespace}_uptime_seconds", "System uptime in seconds")

        # Trackers
        self.request_tracker = RequestTracker(self)
        self.order_tracker = OrderTracker(self)

        # Fill tracking for fill rate calculation
        self._fill_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"placed": 0, "filled": 0}
        )

    # HTTP Request Metrics

    async def record_request_started(self, exchange: str, method: str) -> None:
        """Record that a request has started."""
        await self.active_requests.inc(1.0, exchange=exchange)

    async def record_request_completed(
        self,
        exchange: str,
        method: str,
        endpoint: str,
        duration: float,
        status_code: int,
        error_type: Optional[str] = None,
    ) -> None:
        """Record request completion metrics."""
        await self.active_requests.dec(1.0, exchange=exchange)
        await self.request_count.inc(
            1, exchange=exchange, method=method, endpoint=endpoint, status_code=str(status_code)
        )
        await self.request_latency.observe(
            duration, exchange=exchange, method=method, endpoint=endpoint
        )

        if error_type:
            await self.request_errors.inc(
                1, exchange=exchange, error_type=error_type, status_code=str(status_code)
            )

    def track_request(self, exchange: str, method: str, endpoint: str):
        """Get request tracker context manager."""
        return self.request_tracker.track_request(exchange, method, endpoint)

    # Connection Metrics

    async def record_connection_opened(self, exchange: str, connection_type: str) -> None:
        """Record connection opened."""
        await self.active_connections.inc(1.0, exchange=exchange, connection_type=connection_type)
        await self.connection_attempts.inc(1, exchange=exchange, status="success")

    async def record_connection_closed(self, exchange: str, connection_type: str) -> None:
        """Record connection closed."""
        await self.active_connections.dec(1.0, exchange=exchange, connection_type=connection_type)

    async def record_connection_failed(self, exchange: str, error_type: str) -> None:
        """Record connection failure."""
        await self.connection_attempts.inc(1, exchange=exchange, status="failed")

    async def record_reconnection(self, exchange: str) -> None:
        """Record reconnection."""
        await self.reconnection_count.inc(1, exchange=exchange)

    # Order Metrics

    async def record_order_placement(
        self, exchange: str, symbol: str, order_type: str, duration: float, success: bool
    ) -> None:
        """Record order placement metrics."""
        status = "success" if success else "failed"
        await self.orders_placed.inc(
            1, exchange=exchange, symbol=symbol, order_type=order_type, status=status
        )
        await self.order_placement_latency.observe(
            duration, exchange=exchange, symbol=symbol, order_type=order_type
        )

        # Track for fill rate
        key = f"{exchange}:{symbol}"
        async with self._lock:
            self._fill_stats[key]["placed"] += 1

    async def record_order_filled(
        self, exchange: str, symbol: str, fill_type: str = "full"
    ) -> None:
        """Record order filled."""
        await self.orders_filled.inc(1, exchange=exchange, symbol=symbol, fill_type=fill_type)

        # Track for fill rate
        key = f"{exchange}:{symbol}"
        async with self._lock:
            self._fill_stats[key]["filled"] += 1

    async def record_order_cancelled(self, exchange: str, symbol: str) -> None:
        """Record order cancelled."""
        await self.order_cancel_count.inc(1, exchange=exchange, symbol=symbol)

    async def get_fill_rate(self, exchange: str, symbol: str) -> float:
        """Calculate fill rate for exchange/symbol."""
        key = f"{exchange}:{symbol}"
        async with self._lock:
            stats = self._fill_stats[key]
            if stats["placed"] == 0:
                return 1.0
            return stats["filled"] / stats["placed"]

    async def update_fill_rate_metric(self, exchange: str, symbol: str) -> None:
        """Update fill rate gauge."""
        rate = await self.get_fill_rate(exchange, symbol)
        await self.fill_rate.set(rate, exchange=exchange, symbol=symbol)

    def track_order_placement(self, exchange: str, symbol: str, order_type: str):
        """Get order placement tracker context manager."""
        return self.order_tracker.track_order_placement(exchange, symbol, order_type)

    # Rate Limit Metrics

    async def record_rate_limit_hit(self, exchange: str) -> None:
        """Record rate limit hit."""
        await self.rate_limit_hits.inc(1, exchange=exchange)

    async def record_rate_limit_wait(self, exchange: str, wait_time: float) -> None:
        """Record rate limit wait time."""
        await self.rate_limit_wait_time.observe(wait_time, exchange=exchange)

    # WebSocket Metrics

    async def record_ws_message_received(self, exchange: str, channel: str) -> None:
        """Record WebSocket message received."""
        await self.ws_messages_received.inc(1, exchange=exchange, channel=channel)

    async def record_ws_message_sent(self, exchange: str) -> None:
        """Record WebSocket message sent."""
        await self.ws_messages_sent.inc(1, exchange=exchange)

    async def record_ws_error(self, exchange: str, error_type: str) -> None:
        """Record WebSocket error."""
        await self.ws_errors.inc(1, exchange=exchange, error_type=error_type)

    # Trading Metrics

    async def record_trade(
        self, exchange: str, symbol: str, side: str, quantity: Decimal, price: Decimal
    ) -> None:
        """Record trade execution."""
        volume = float(quantity * price)
        await self.trade_volume.inc(volume, exchange=exchange, symbol=symbol, side=side)
        await self.trade_count.inc(1, exchange=exchange, symbol=symbol, side=side)

    async def update_position(self, exchange: str, symbol: str, size: Decimal) -> None:
        """Update position size."""
        await self.position_size.set(float(size), exchange=exchange, symbol=symbol)

    async def update_pnl(self, exchange: str, symbol: str, pnl: Decimal) -> None:
        """Update P&L."""
        await self.pnl.set(float(pnl), exchange=exchange, symbol=symbol)

    # System Metrics

    async def update_uptime(self) -> None:
        """Update uptime metric."""
        uptime = time.time() - self._started_at
        await self.uptime_seconds.set(uptime)

    # Data Retrieval

    async def get_all_metrics(self) -> Dict[str, Any]:
        """Get all metrics as dictionary."""
        await self.update_uptime()

        return {
            "http_requests": await self.request_count.get_all(),
            "http_request_latency": await self.request_latency.get_all_snapshots(),
            "http_errors": await self.request_errors.get_all(),
            "active_requests": await self.active_requests.get_all(),
            "active_connections": await self.active_connections.get_all(),
            "connection_attempts": await self.connection_attempts.get_all(),
            "reconnections": await self.reconnection_count.get_all(),
            "orders_placed": await self.orders_placed.get_all(),
            "order_placement_latency": await self.order_placement_latency.get_all_snapshots(),
            "orders_filled": await self.orders_filled.get_all(),
            "fill_rate": await self.fill_rate.get_all(),
            "orders_cancelled": await self.order_cancel_count.get_all(),
            "rate_limit_hits": await self.rate_limit_hits.get_all(),
            "ws_messages_received": await self.ws_messages_received.get_all(),
            "ws_messages_sent": await self.ws_messages_sent.get_all(),
            "ws_errors": await self.ws_errors.get_all(),
            "trade_volume": await self.trade_volume.get_all(),
            "trade_count": await self.trade_count.get_all(),
            "position_size": await self.position_size.get_all(),
            "pnl": await self.pnl.get_all(),
            "uptime_seconds": await self.uptime_seconds.get(),
        }


# Global collector instance
collector: Optional[MetricsCollector] = None


def init_collector(namespace: str = "algo_trading") -> MetricsCollector:
    """Initialize global metrics collector."""
    global collector
    collector = MetricsCollector(namespace)
    return collector


def get_collector() -> MetricsCollector:
    """Get global metrics collector."""
    if collector is None:
        return init_collector()
    return collector
