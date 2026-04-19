"""Metric exporters for the algo-trading system.

This module provides multiple export formats:
- Prometheus exporter (/metrics endpoint)
- StatsD exporter (UDP)
- In-memory store for querying
- Log exporter (structured logging)
"""

import asyncio
import json
import socket
import time
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Callable, Union
from dataclasses import asdict
from datetime import datetime

import httpx
import structlog

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
    start_http_server,
)
from prometheus_client.exposition import make_wsgi_app

from src.metrics.collector import MetricsCollector, MetricType, MetricValue


logger = structlog.get_logger(__name__)


class BaseExporter(ABC):
    """Base class for metric exporters."""

    @abstractmethod
    async def export(self, metrics: Dict[str, Any]) -> None:
        """Export metrics."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the exporter."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the exporter."""
        pass


class PrometheusExporter(BaseExporter):
    """Prometheus metrics exporter.

    Exposes metrics on an HTTP endpoint (default /metrics) in Prometheus format.
    """

    def __init__(
        self,
        collector: MetricsCollector,
        port: int = 9090,
        host: str = "0.0.0.0",
        endpoint: str = "/metrics",
    ):
        """Initialize Prometheus exporter.

        Args:
            collector: Metrics collector instance
            port: HTTP server port
            host: HTTP server host
            endpoint: Metrics endpoint path
        """
        self.collector = collector
        self.port = port
        self.host = host
        self.endpoint = endpoint
        self._server = None
        self._running = False
        self._prometheus_counters: Dict[str, Counter] = {}
        self._prometheus_gauges: Dict[str, Gauge] = {}
        self._prometheus_histograms: Dict[str, Histogram] = {}
        self._prometheus_info: Dict[str, Info] = {}

        # Initialize Prometheus metrics from collector
        self._init_prometheus_metrics()

    def _init_prometheus_metrics(self) -> None:
        """Initialize Prometheus metric objects."""
        namespace = self.collector.namespace

        # HTTP request metrics
        self._prometheus_counters["http_requests"] = Counter(
            f"{namespace}_http_requests_total",
            "Total HTTP requests",
            ["exchange", "method", "endpoint", "status_code"],
            registry=self.collector.registry,
        )
        self._prometheus_histograms["request_latency"] = Histogram(
            f"{namespace}_http_request_duration_seconds",
            "HTTP request latency in seconds",
            ["exchange", "method", "endpoint"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self.collector.registry,
        )
        self._prometheus_counters["request_errors"] = Counter(
            f"{namespace}_http_request_errors_total",
            "Total HTTP request errors",
            ["exchange", "error_type", "status_code"],
            registry=self.collector.registry,
        )
        self._prometheus_gauges["active_requests"] = Gauge(
            f"{namespace}_http_active_requests",
            "Number of active HTTP requests",
            ["exchange"],
            registry=self.collector.registry,
        )

        # Connection metrics
        self._prometheus_gauges["active_connections"] = Gauge(
            f"{namespace}_active_connections",
            "Number of active connections",
            ["exchange", "connection_type"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["connection_attempts"] = Counter(
            f"{namespace}_connection_attempts_total",
            "Total connection attempts",
            ["exchange", "status"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["reconnections"] = Counter(
            f"{namespace}_reconnections_total",
            "Total reconnections",
            ["exchange"],
            registry=self.collector.registry,
        )

        # Order metrics
        self._prometheus_histograms["order_placement_latency"] = Histogram(
            f"{namespace}_order_placement_duration_seconds",
            "Order placement latency in seconds",
            ["exchange", "symbol", "order_type"],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
            registry=self.collector.registry,
        )
        self._prometheus_counters["orders_placed"] = Counter(
            f"{namespace}_orders_placed_total",
            "Total orders placed",
            ["exchange", "symbol", "order_type", "status"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["orders_filled"] = Counter(
            f"{namespace}_orders_filled_total",
            "Total orders filled",
            ["exchange", "symbol", "fill_type"],
            registry=self.collector.registry,
        )
        self._prometheus_gauges["fill_rate"] = Gauge(
            f"{namespace}_fill_rate",
            "Order fill rate",
            ["exchange", "symbol"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["orders_cancelled"] = Counter(
            f"{namespace}_orders_cancelled_total",
            "Total orders cancelled",
            ["exchange", "symbol"],
            registry=self.collector.registry,
        )

        # Rate limit metrics
        self._prometheus_counters["rate_limit_hits"] = Counter(
            f"{namespace}_rate_limit_hits_total",
            "Total rate limit hits",
            ["exchange"],
            registry=self.collector.registry,
        )

        # WebSocket metrics
        self._prometheus_counters["ws_messages_received"] = Counter(
            f"{namespace}_ws_messages_received_total",
            "Total WebSocket messages received",
            ["exchange", "channel"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["ws_messages_sent"] = Counter(
            f"{namespace}_ws_messages_sent_total",
            "Total WebSocket messages sent",
            ["exchange"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["ws_errors"] = Counter(
            f"{namespace}_ws_errors_total",
            "Total WebSocket errors",
            ["exchange", "error_type"],
            registry=self.collector.registry,
        )

        # Trading metrics
        self._prometheus_counters["trade_volume"] = Counter(
            f"{namespace}_trade_volume",
            "Trading volume",
            ["exchange", "symbol", "side"],
            registry=self.collector.registry,
        )
        self._prometheus_counters["trades"] = Counter(
            f"{namespace}_trades_total",
            "Total trades executed",
            ["exchange", "symbol", "side"],
            registry=self.collector.registry,
        )
        self._prometheus_gauges["position_size"] = Gauge(
            f"{namespace}_position_size",
            "Current position size",
            ["exchange", "symbol"],
            registry=self.collector.registry,
        )
        self._prometheus_gauges["pnl"] = Gauge(
            f"{namespace}_pnl",
            "Profit and loss",
            ["exchange", "symbol"],
            registry=self.collector.registry,
        )

        # System metrics
        self._prometheus_gauges["uptime"] = Gauge(
            f"{namespace}_uptime_seconds",
            "System uptime in seconds",
            registry=self.collector.registry,
        )

    async def start(self) -> None:
        """Start Prometheus HTTP server."""
        if self._running:
            return

        # Start HTTP server in a separate thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: start_http_server(self.port, self.host, registry=self.collector.registry)
        )
        self._running = True
        logger.info(f"Prometheus metrics server started on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop Prometheus HTTP server."""
        self._running = False
        logger.info("Prometheus metrics server stopped")

    async def export(self, metrics: Dict[str, Any]) -> None:
        """Export metrics to Prometheus (automatic via registry)."""
        # Prometheus metrics are automatically updated via the registry
        # This method can be used for bulk updates if needed
        pass

    def get_metrics_text(self) -> str:
        """Get metrics in Prometheus text format."""
        return generate_latest(self.collector.registry).decode("utf-8")


class StatsDExporter(BaseExporter):
    """StatsD metrics exporter via UDP.

    Sends metrics to a StatsD-compatible collector.
    """

    def __init__(
        self,
        collector: MetricsCollector,
        host: str = "localhost",
        port: int = 8125,
        prefix: str = "algo_trading",
        buffer_size: int = 100,
    ):
        """Initialize StatsD exporter.

        Args:
            collector: Metrics collector instance
            host: StatsD host
            port: StatsD port
            prefix: Metric name prefix
            buffer_size: UDP packet buffer size
        """
        self.collector = collector
        self.host = host
        self.port = port
        self.prefix = prefix
        self.buffer_size = buffer_size
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._buffer: List[str] = []
        self._lock = asyncio.Lock()
        self._flush_interval = 1.0  # Flush buffer every second
        self._flush_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start StatsD exporter."""
        if self._running:
            return

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._running = True

        # Start flush task
        self._flush_task = asyncio.create_task(self._flush_loop())

        logger.info(f"StatsD exporter started ({self.host}:{self.port})")

    async def stop(self) -> None:
        """Stop StatsD exporter."""
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Flush remaining metrics
        await self._flush_buffer()

        if self._socket:
            self._socket.close()

        logger.info("StatsD exporter stopped")

    async def export(self, metrics: Dict[str, Any]) -> None:
        """Export metrics to StatsD."""
        statsd_metrics = self._convert_to_statsd(metrics)

        async with self._lock:
            self._buffer.extend(statsd_metrics)

            # Flush if buffer is full
            if len(self._buffer) >= self.buffer_size:
                await self._flush_buffer()

    def _convert_to_statsd(self, metrics: Dict[str, Any]) -> List[str]:
        """Convert metrics to StatsD format."""
        lines = []
        timestamp = int(time.time())

        # Counter metrics
        if "http_requests" in metrics:
            for key, value in metrics["http_requests"].items():
                lines.append(f"{self.prefix}.http.requests:{value}|c")

        if "http_errors" in metrics:
            for key, value in metrics["http_errors"].items():
                lines.append(f"{self.prefix}.http.errors:{value}|c")

        if "orders_placed" in metrics:
            for key, value in metrics["orders_placed"].items():
                lines.append(f"{self.prefix}.orders.placed:{value}|c")

        if "orders_filled" in metrics:
            for key, value in metrics["orders_filled"].items():
                lines.append(f"{self.prefix}.orders.filled:{value}|c")

        if "ws_messages_received" in metrics:
            for key, value in metrics["ws_messages_received"].items():
                lines.append(f"{self.prefix}.ws.messages.received:{value}|c")

        if "ws_messages_sent" in metrics:
            for key, value in metrics["ws_messages_sent"].items():
                lines.append(f"{self.prefix}.ws.messages.sent:{value}|c")

        # Gauge metrics
        if "active_requests" in metrics:
            for key, value in metrics["active_requests"].items():
                lines.append(f"{self.prefix}.http.active_requests:{value}|g")

        if "active_connections" in metrics:
            for key, value in metrics["active_connections"].items():
                lines.append(f"{self.prefix}.active_connections:{value}|g")

        if "fill_rate" in metrics:
            for key, value in metrics["fill_rate"].items():
                lines.append(f"{self.prefix}.fill_rate:{value}|g")

        if "position_size" in metrics:
            for key, value in metrics["position_size"].items():
                lines.append(f"{self.prefix}.position.size:{value}|g")

        if "pnl" in metrics:
            for key, value in metrics["pnl"].items():
                lines.append(f"{self.prefix}.pnl:{value}|g")

        if "uptime_seconds" in metrics:
            lines.append(f"{self.prefix}.uptime:{metrics['uptime_seconds']}|g")

        # Histogram/timer metrics (convert to StatsD timers)
        if "http_request_latency" in metrics:
            for key, snapshot in metrics["http_request_latency"].items():
                if snapshot:
                    lines.append(f"{self.prefix}.http.latency:{snapshot.p50}|ms")

        if "order_placement_latency" in metrics:
            for key, snapshot in metrics["order_placement_latency"].items():
                if snapshot:
                    lines.append(f"{self.prefix}.order.latency:{snapshot.p50}|ms")

        return lines

    async def _flush_loop(self) -> None:
        """Background task to flush metrics periodically."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in StatsD flush loop: {e}")

    async def _flush_buffer(self) -> None:
        """Flush buffered metrics to StatsD."""
        async with self._lock:
            if not self._buffer or not self._socket:
                return

            # Join metrics into packets (respecting MTU)
            packet = "\n".join(self._buffer)
            self._buffer = []

        try:
            loop = asyncio.get_event_loop()
            await loop.sock_sendto(self._socket, packet.encode("utf-8"), (self.host, self.port))
        except Exception as e:
            logger.error(f"Error sending metrics to StatsD: {e}")

    async def send_metric(
        self,
        metric_type: str,
        name: str,
        value: Union[int, float],
        sample_rate: float = 1.0,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Send a single metric to StatsD.

        Args:
            metric_type: Metric type (counter, gauge, timer, histogram)
            name: Metric name
            value: Metric value
            sample_rate: Sample rate (0-1)
            tags: Optional tags
        """
        if not self._running or not self._socket:
            return

        type_code = {"counter": "c", "gauge": "g", "timer": "ms", "histogram": "h"}.get(
            metric_type, "g"
        )

        line = f"{self.prefix}.{name}:{value}|{type_code}"

        if sample_rate != 1.0:
            line += f"|@{sample_rate}"

        if tags:
            tag_str = ",".join(f"{k}:{v}" for k, v in tags.items())
            line += f"|#{tag_str}"

        async with self._lock:
            self._buffer.append(line)

            if len(self._buffer) >= self.buffer_size:
                await self._flush_buffer()


class InMemoryStore(BaseExporter):
    """In-memory metric store for querying.

    Stores metrics in memory with configurable retention for quick queries.
    """

    def __init__(
        self,
        collector: MetricsCollector,
        max_data_points: int = 10000,
        retention_seconds: int = 3600,
    ):
        """Initialize in-memory store.

        Args:
            collector: Metrics collector instance
            max_data_points: Maximum data points to store per metric
            retention_seconds: How long to retain data points
        """
        self.collector = collector
        self.max_data_points = max_data_points
        self.retention_seconds = retention_seconds
        self._data: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start in-memory store."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("In-memory metric store started")

    async def stop(self) -> None:
        """Stop in-memory store."""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        logger.info("In-memory metric store stopped")

    async def export(self, metrics: Dict[str, Any]) -> None:
        """Store metrics in memory."""
        timestamp = time.time()

        async with self._lock:
            for metric_name, value in metrics.items():
                if metric_name not in self._data:
                    self._data[metric_name] = []

                self._data[metric_name].append({"timestamp": timestamp, "value": value})

                # Trim to max data points
                if len(self._data[metric_name]) > self.max_data_points:
                    self._data[metric_name] = self._data[metric_name][-self.max_data_points :]

    async def query(
        self,
        metric_name: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query stored metrics.

        Args:
            metric_name: Name of the metric
            start_time: Start timestamp (optional)
            end_time: End timestamp (optional)
            limit: Maximum number of results

        Returns:
            List of metric data points
        """
        async with self._lock:
            if metric_name not in self._data:
                return []

            data = self._data[metric_name]

            # Filter by time range
            if start_time:
                data = [d for d in data if d["timestamp"] >= start_time]
            if end_time:
                data = [d for d in data if d["timestamp"] <= end_time]

            # Return last N results
            return data[-limit:]

    async def get_latest(self, metric_name: str) -> Optional[Dict[str, Any]]:
        """Get latest value for a metric.

        Args:
            metric_name: Name of the metric

        Returns:
            Latest metric data point or None
        """
        async with self._lock:
            if metric_name not in self._data or not self._data[metric_name]:
                return None
            return self._data[metric_name][-1]

    async def get_metric_names(self) -> List[str]:
        """Get list of all metric names."""
        async with self._lock:
            return list(self._data.keys())

    async def _cleanup_loop(self) -> None:
        """Background task to clean up old data."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Clean up every minute
                await self._cleanup_old_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_old_data(self) -> None:
        """Remove data points older than retention period."""
        cutoff = time.time() - self.retention_seconds

        async with self._lock:
            for metric_name in self._data:
                self._data[metric_name] = [
                    d for d in self._data[metric_name] if d["timestamp"] >= cutoff
                ]


class LogExporter(BaseExporter):
    """Log exporter for structured logging of metrics.

    Exports metrics as structured log entries for log aggregation systems.
    """

    def __init__(
        self,
        collector: MetricsCollector,
        log_interval: float = 60.0,
        include_metrics: Optional[List[str]] = None,
        exclude_metrics: Optional[List[str]] = None,
        log_level: str = "INFO",
    ):
        """Initialize log exporter.

        Args:
            collector: Metrics collector instance
            log_interval: How often to log metrics (seconds)
            include_metrics: Only include these metrics (None = all)
            exclude_metrics: Exclude these metrics
            log_level: Log level for metric entries
        """
        self.collector = collector
        self.log_interval = log_interval
        self.include_metrics = include_metrics
        self.exclude_metrics = exclude_metrics or []
        self.log_level = log_level.upper()
        self._running = False
        self._log_task: Optional[asyncio.Task] = None
        self._logger = structlog.get_logger("metrics")

    async def start(self) -> None:
        """Start log exporter."""
        if self._running:
            return

        self._running = True
        self._log_task = asyncio.create_task(self._log_loop())
        logger.info(f"Log exporter started (interval: {self.log_interval}s)")

    async def stop(self) -> None:
        """Stop log exporter."""
        self._running = False

        if self._log_task:
            self._log_task.cancel()
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass

        logger.info("Log exporter stopped")

    async def export(self, metrics: Dict[str, Any]) -> None:
        """Export metrics to log (immediate)."""
        await self._log_metrics(metrics)

    async def _log_loop(self) -> None:
        """Background task to periodically log metrics."""
        while self._running:
            try:
                await asyncio.sleep(self.log_interval)
                metrics = await self.collector.get_all_metrics()
                await self._log_metrics(metrics)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in log loop: {e}")

    async def _log_metrics(self, metrics: Dict[str, Any]) -> None:
        """Log metrics as structured entries."""
        # Filter metrics
        filtered_metrics = {}
        for name, value in metrics.items():
            if self.include_metrics and name not in self.include_metrics:
                continue
            if name in self.exclude_metrics:
                continue
            filtered_metrics[name] = value

        # Create log entry
        log_entry = {
            "event": "metrics_snapshot",
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": filtered_metrics,
        }

        # Log at appropriate level
        log_method = getattr(self._logger, self.log_level.lower(), self._logger.info)
        log_method("metrics_snapshot", **log_entry)


class CompositeExporter(BaseExporter):
    """Composite exporter that sends metrics to multiple exporters."""

    def __init__(self, exporters: List[BaseExporter]):
        """Initialize composite exporter.

        Args:
            exporters: List of exporters to use
        """
        self.exporters = exporters

    async def start(self) -> None:
        """Start all exporters."""
        await asyncio.gather(*[e.start() for e in self.exporters])

    async def stop(self) -> None:
        """Stop all exporters."""
        await asyncio.gather(*[e.stop() for e in self.exporters])

    async def export(self, metrics: Dict[str, Any]) -> None:
        """Export metrics to all exporters."""
        await asyncio.gather(*[e.export(metrics) for e in self.exporters])

    def add_exporter(self, exporter: BaseExporter) -> None:
        """Add an exporter."""
        self.exporters.append(exporter)

    def remove_exporter(self, exporter: BaseExporter) -> None:
        """Remove an exporter."""
        if exporter in self.exporters:
            self.exporters.remove(exporter)


class MetricsExportManager:
    """Manager for handling multiple metric exporters."""

    def __init__(
        self,
        collector: MetricsCollector,
        export_interval: float = 10.0,
        enable_prometheus: bool = True,
        enable_statsd: bool = False,
        enable_in_memory: bool = True,
        enable_logging: bool = True,
        prometheus_port: int = 9090,
        statsd_host: str = "localhost",
        statsd_port: int = 8125,
    ):
        """Initialize metrics export manager.

        Args:
            collector: Metrics collector instance
            export_interval: How often to export metrics (seconds)
            enable_prometheus: Enable Prometheus exporter
            enable_statsd: Enable StatsD exporter
            enable_in_memory: Enable in-memory store
            enable_logging: Enable log exporter
            prometheus_port: Prometheus HTTP port
            statsd_host: StatsD host
            statsd_port: StatsD port
        """
        self.collector = collector
        self.export_interval = export_interval
        self._running = False
        self._export_task: Optional[asyncio.Task] = None

        # Create exporters
        self.exporters: List[BaseExporter] = []

        if enable_prometheus:
            self.prometheus = PrometheusExporter(collector, port=prometheus_port)
            self.exporters.append(self.prometheus)
        else:
            self.prometheus = None

        if enable_statsd:
            self.statsd = StatsDExporter(collector, host=statsd_host, port=statsd_port)
            self.exporters.append(self.statsd)
        else:
            self.statsd = None

        if enable_in_memory:
            self.in_memory = InMemoryStore(collector)
            self.exporters.append(self.in_memory)
        else:
            self.in_memory = None

        if enable_logging:
            self.log_exporter = LogExporter(collector)
            self.exporters.append(self.log_exporter)
        else:
            self.log_exporter = None

        self.composite = CompositeExporter(self.exporters)

    async def start(self) -> None:
        """Start all exporters and export loop."""
        if self._running:
            return

        self._running = True
        await self.composite.start()

        # Start export loop
        self._export_task = asyncio.create_task(self._export_loop())

        logger.info(f"Metrics export manager started (interval: {self.export_interval}s)")

    async def stop(self) -> None:
        """Stop all exporters."""
        self._running = False

        if self._export_task:
            self._export_task.cancel()
            try:
                await self._export_task
            except asyncio.CancelledError:
                pass

        await self.composite.stop()
        logger.info("Metrics export manager stopped")

    async def export_now(self) -> None:
        """Export metrics immediately."""
        metrics = await self.collector.get_all_metrics()
        await self.composite.export(metrics)

    async def _export_loop(self) -> None:
        """Background task to periodically export metrics."""
        while self._running:
            try:
                await asyncio.sleep(self.export_interval)
                await self.export_now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in export loop: {e}")

    def get_prometheus_metrics(self) -> str:
        """Get Prometheus-formatted metrics (if enabled)."""
        if self.prometheus:
            return self.prometheus.get_metrics_text()
        return ""

    async def query_in_memory(
        self,
        metric_name: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query in-memory store (if enabled)."""
        if self.in_memory:
            return await self.in_memory.query(metric_name, start_time, end_time, limit)
        return []


# Global export manager instance
export_manager: Optional[MetricsExportManager] = None


def init_exporters(
    collector: MetricsCollector,
    export_interval: float = 10.0,
    enable_prometheus: bool = True,
    enable_statsd: bool = False,
    enable_in_memory: bool = True,
    enable_logging: bool = True,
    **kwargs,
) -> MetricsExportManager:
    """Initialize global export manager."""
    global export_manager
    export_manager = MetricsExportManager(
        collector=collector,
        export_interval=export_interval,
        enable_prometheus=enable_prometheus,
        enable_statsd=enable_statsd,
        enable_in_memory=enable_in_memory,
        enable_logging=enable_logging,
        **kwargs,
    )
    return export_manager


def get_export_manager() -> Optional[MetricsExportManager]:
    """Get global export manager."""
    return export_manager
