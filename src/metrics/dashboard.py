"""Dashboard data aggregation for real-time monitoring.

This module provides:
- DashboardData class aggregating key metrics
- Real-time health status per exchange
- Circuit breaker status
- Rate limit utilization
- Daily/weekly trading metrics
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta

import structlog

from src.metrics.collector import MetricsCollector


logger = structlog.get_logger(__name__)


class HealthStatus(str, Enum):
    """Health status enumeration."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CircuitState(str, Enum):
    """Circuit breaker state enumeration."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    UNKNOWN = "unknown"


@dataclass
class ExchangeHealth:
    """Health status for a single exchange."""
    exchange: str
    status: HealthStatus
    is_connected: bool
    latency_ms: Optional[float]
    avg_latency_ms: float
    error_rate: float
    last_error: Optional[str]
    last_error_time: Optional[float]
    uptime_seconds: float
    connection_success_rate: float
    circuit_state: CircuitState = CircuitState.UNKNOWN
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'exchange': self.exchange,
            'status': self.status.value,
            'is_connected': self.is_connected,
            'latency_ms': self.latency_ms,
            'avg_latency_ms': self.avg_latency_ms,
            'error_rate': self.error_rate,
            'last_error': self.last_error,
            'last_error_time': self.last_error_time,
            'uptime_seconds': self.uptime_seconds,
            'connection_success_rate': self.connection_success_rate,
            'circuit_state': self.circuit_state.value
        }


@dataclass
class RateLimitStatus:
    """Rate limit status for an exchange."""
    exchange: str
    current_usage: float  # 0-1
    remaining: int
    limit: int
    reset_time: Optional[float]
    hit_count: int
    wait_time_avg: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'exchange': self.exchange,
            'current_usage': self.current_usage,
            'remaining': self.remaining,
            'limit': self.limit,
            'reset_time': self.reset_time,
            'hit_count': self.hit_count,
            'wait_time_avg': self.wait_time_avg,
            'utilization_pct': self.current_usage * 100
        }


@dataclass
class TradingSummary:
    """Trading summary for a time period."""
    period: str  # "daily", "weekly", "monthly"
    start_time: float
    end_time: float
    total_trades: int
    total_volume: float
    avg_trade_size: float
    pnl: float
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    sharpe_ratio: Optional[float]
    max_drawdown: Optional[float]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'period': self.period,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'total_trades': self.total_trades,
            'total_volume': self.total_volume,
            'avg_trade_size': self.avg_trade_size,
            'pnl': self.pnl,
            'win_count': self.win_count,
            'loss_count': self.loss_count,
            'win_rate': self.win_rate,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'profit_factor': self.profit_factor,
            'sharpe_ratio': self.sharpe_ratio,
            'max_drawdown': self.max_drawdown
        }


@dataclass
class OrderMetrics:
    """Order metrics for an exchange."""
    exchange: str
    total_placed: int
    total_filled: int
    total_cancelled: int
    fill_rate: float
    avg_placement_latency_ms: float
    p95_placement_latency_ms: float
    p99_placement_latency_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'exchange': self.exchange,
            'total_placed': self.total_placed,
            'total_filled': self.total_filled,
            'total_cancelled': self.total_cancelled,
            'fill_rate': self.fill_rate,
            'avg_placement_latency_ms': self.avg_placement_latency_ms,
            'p95_placement_latency_ms': self.p95_placement_latency_ms,
            'p99_placement_latency_ms': self.p99_placement_latency_ms
        }


@dataclass
class ConnectionMetrics:
    """Connection metrics for an exchange."""
    exchange: str
    active_connections: int
    total_attempts: int
    successful_connections: int
    failed_connections: int
    reconnections: int
    connection_success_rate: float
    messages_sent: int
    messages_received: int
    ws_errors: int
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'exchange': self.exchange,
            'active_connections': self.active_connections,
            'total_attempts': self.total_attempts,
            'successful_connections': self.successful_connections,
            'failed_connections': self.failed_connections,
            'reconnections': self.reconnections,
            'connection_success_rate': self.connection_success_rate,
            'messages_sent': self.messages_sent,
            'messages_received': self.messages_received,
            'ws_errors': self.ws_errors
        }


@dataclass
class SystemOverview:
    """System overview metrics."""
    uptime_seconds: float
    total_exchanges: int
    healthy_exchanges: int
    degraded_exchanges: int
    unhealthy_exchanges: int
    total_active_connections: int
    total_active_requests: int
    circuit_breakers_open: int
    overall_health: HealthStatus
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'uptime_seconds': self.uptime_seconds,
            'total_exchanges': self.total_exchanges,
            'healthy_exchanges': self.healthy_exchanges,
            'degraded_exchanges': self.degraded_exchanges,
            'unhealthy_exchanges': self.unhealthy_exchanges,
            'total_active_connections': self.total_active_connections,
            'total_active_requests': self.total_active_requests,
            'circuit_breakers_open': self.circuit_breakers_open,
            'overall_health': self.overall_health.value
        }


@dataclass
class DashboardSnapshot:
    """Complete dashboard snapshot."""
    timestamp: float
    system: SystemOverview
    exchanges: Dict[str, ExchangeHealth]
    rate_limits: Dict[str, RateLimitStatus]
    order_metrics: Dict[str, OrderMetrics]
    connection_metrics: Dict[str, ConnectionMetrics]
    trading_summary: Dict[str, TradingSummary]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'timestamp': self.timestamp,
            'system': self.system.to_dict(),
            'exchanges': {k: v.to_dict() for k, v in self.exchanges.items()},
            'rate_limits': {k: v.to_dict() for k, v in self.rate_limits.items()},
            'order_metrics': {k: v.to_dict() for k, v in self.order_metrics.items()},
            'connection_metrics': {k: v.to_dict() for k, v in self.connection_metrics.items()},
            'trading_summary': {k: v.to_dict() for k, v in self.trading_summary.items()}
        }


class DashboardData:
    """Dashboard data aggregator for real-time monitoring.
    
    Provides a consolidated view of:
    - System health and status
    - Per-exchange metrics
    - Circuit breaker states
    - Rate limit utilization
    - Trading performance
    """
    
    def __init__(
        self,
        collector: MetricsCollector,
        health_check_interval: float = 5.0,
        max_latency_threshold_ms: float = 1000.0,
        max_error_rate: float = 0.1
    ):
        """Initialize dashboard data aggregator.
        
        Args:
            collector: Metrics collector instance
            health_check_interval: How often to refresh health status
            max_latency_threshold_ms: Latency threshold for degraded status
            max_error_rate: Error rate threshold for unhealthy status
        """
        self.collector = collector
        self.health_check_interval = health_check_interval
        self.max_latency_threshold_ms = max_latency_threshold_ms
        self.max_error_rate = max_error_rate
        
        # Internal state
        self._exchange_health: Dict[str, ExchangeHealth] = {}
        self._rate_limits: Dict[str, RateLimitStatus] = {}
        self._order_metrics: Dict[str, OrderMetrics] = {}
        self._connection_metrics: Dict[str, ConnectionMetrics] = {}
        self._trading_history: List[Dict[str, Any]] = []
        self._pnl_history: Dict[str, List[float]] = defaultdict(list)
        
        # Known exchanges
        self._exchanges: Set[str] = set()
        
        # Background task
        self._refresh_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Circuit breaker states (populated externally)
        self._circuit_states: Dict[str, CircuitState] = {}
        
        # Rate limit info (populated externally)
        self._rate_limit_info: Dict[str, Dict[str, Any]] = {}
    
    async def start(self) -> None:
        """Start dashboard data collection."""
        if self._running:
            return
        
        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Dashboard data collection started")
    
    async def stop(self) -> None:
        """Stop dashboard data collection."""
        self._running = False
        
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Dashboard data collection stopped")
    
    def register_exchange(self, exchange: str) -> None:
        """Register an exchange for monitoring."""
        self._exchanges.add(exchange)
        logger.info(f"Registered exchange for dashboard: {exchange}")
    
    def unregister_exchange(self, exchange: str) -> None:
        """Unregister an exchange."""
        self._exchanges.discard(exchange)
        self._exchange_health.pop(exchange, None)
        self._rate_limits.pop(exchange, None)
        self._order_metrics.pop(exchange, None)
        self._connection_metrics.pop(exchange, None)
    
    def update_circuit_state(self, exchange: str, state: CircuitState) -> None:
        """Update circuit breaker state for an exchange."""
        self._circuit_states[exchange] = state
    
    def update_rate_limit_info(
        self,
        exchange: str,
        remaining: int,
        limit: int,
        reset_time: Optional[float] = None
    ) -> None:
        """Update rate limit information."""
        self._rate_limit_info[exchange] = {
            'remaining': remaining,
            'limit': limit,
            'reset_time': reset_time,
            'updated_at': time.time()
        }
    
    async def get_exchange_health(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get health status for one or all exchanges.
        
        Args:
            exchange: Specific exchange or None for all
            
        Returns:
            Health status dictionary
        """
        if exchange:
            if exchange in self._exchange_health:
                return self._exchange_health[exchange].to_dict()
            return {'error': f'Exchange not found: {exchange}'}
        
        return {
            'exchanges': {k: v.to_dict() for k, v in self._exchange_health.items()},
            'summary': {
                'total': len(self._exchange_health),
                'healthy': sum(1 for h in self._exchange_health.values() 
                              if h.status == HealthStatus.HEALTHY),
                'degraded': sum(1 for h in self._exchange_health.values() 
                               if h.status == HealthStatus.DEGRADED),
                'unhealthy': sum(1 for h in self._exchange_health.values() 
                                if h.status == HealthStatus.UNHEALTHY)
            }
        }
    
    async def get_circuit_breaker_status(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get circuit breaker status.
        
        Args:
            exchange: Specific exchange or None for all
            
        Returns:
            Circuit breaker status dictionary
        """
        if exchange:
            state = self._circuit_states.get(exchange, CircuitState.UNKNOWN)
            return {
                'exchange': exchange,
                'state': state.value,
                'is_open': state == CircuitState.OPEN,
                'is_closed': state == CircuitState.CLOSED
            }
        
        return {
            'exchanges': {
                ex: {
                    'state': state.value,
                    'is_open': state == CircuitState.OPEN,
                    'is_closed': state == CircuitState.CLOSED
                }
                for ex, state in self._circuit_states.items()
            },
            'summary': {
                'total': len(self._circuit_states),
                'open': sum(1 for s in self._circuit_states.values() if s == CircuitState.OPEN),
                'half_open': sum(1 for s in self._circuit_states.values() if s == CircuitState.HALF_OPEN),
                'closed': sum(1 for s in self._circuit_states.values() if s == CircuitState.CLOSED)
            }
        }
    
    async def get_rate_limit_status(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get rate limit status.
        
        Args:
            exchange: Specific exchange or None for all
            
        Returns:
            Rate limit status dictionary
        """
        if exchange:
            if exchange in self._rate_limits:
                return self._rate_limits[exchange].to_dict()
            return {'error': f'Exchange not found: {exchange}'}
        
        return {
            'exchanges': {k: v.to_dict() for k, v in self._rate_limits.items()},
            'summary': {
                'avg_utilization': sum(r.current_usage for r in self._rate_limits.values()) / len(self._rate_limits)
                if self._rate_limits else 0
            }
        }
    
    async def get_trading_summary(
        self,
        period: str = "daily"
    ) -> Dict[str, Any]:
        """Get trading summary for a period.
        
        Args:
            period: "daily", "weekly", or "monthly"
            
        Returns:
            Trading summary dictionary
        """
        if period in self._trading_summary_cache:
            return self._trading_summary_cache[period].to_dict()
        
        # Calculate from metrics
        summary = await self._calculate_trading_summary(period)
        return summary.to_dict()
    
    async def get_order_metrics(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get order metrics.
        
        Args:
            exchange: Specific exchange or None for all
            
        Returns:
            Order metrics dictionary
        """
        if exchange:
            if exchange in self._order_metrics:
                return self._order_metrics[exchange].to_dict()
            return {'error': f'Exchange not found: {exchange}'}
        
        return {
            'exchanges': {k: v.to_dict() for k, v in self._order_metrics.items()},
            'summary': {
                'total_placed': sum(o.total_placed for o in self._order_metrics.values()),
                'total_filled': sum(o.total_filled for o in self._order_metrics.values()),
                'avg_fill_rate': sum(o.fill_rate for o in self._order_metrics.values()) / len(self._order_metrics)
                if self._order_metrics else 0
            }
        }
    
    async def get_system_overview(self) -> Dict[str, Any]:
        """Get system overview."""
        await self._refresh_metrics()
        overview = self._calculate_system_overview()
        return overview.to_dict()
    
    async def get_full_snapshot(self) -> Dict[str, Any]:
        """Get full dashboard snapshot."""
        await self._refresh_metrics()
        
        snapshot = DashboardSnapshot(
            timestamp=time.time(),
            system=self._calculate_system_overview(),
            exchanges=self._exchange_health.copy(),
            rate_limits=self._rate_limits.copy(),
            order_metrics=self._order_metrics.copy(),
            connection_metrics=self._connection_metrics.copy(),
            trading_summary=self._trading_summary_cache.copy()
        )
        
        return snapshot.to_dict()
    
    async def _refresh_loop(self) -> None:
        """Background task to refresh dashboard data."""
        while self._running:
            try:
                await self._refresh_metrics()
                await asyncio.sleep(self.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in dashboard refresh loop: {e}")
                await asyncio.sleep(self.health_check_interval)
    
    async def _refresh_metrics(self) -> None:
        """Refresh all dashboard metrics."""
        metrics = await self.collector.get_all_metrics()
        
        # Refresh exchange health
        await self._refresh_exchange_health(metrics)
        
        # Refresh rate limits
        await self._refresh_rate_limits(metrics)
        
        # Refresh order metrics
        await self._refresh_order_metrics(metrics)
        
        # Refresh connection metrics
        await self._refresh_connection_metrics(metrics)
        
        # Refresh trading summaries
        await self._refresh_trading_summaries(metrics)
    
    async def _refresh_exchange_health(self, metrics: Dict[str, Any]) -> None:
        """Refresh exchange health status."""
        active_connections = metrics.get('active_connections', {})
        connection_attempts = metrics.get('connection_attempts', {})
        request_errors = metrics.get('http_errors', {})
        request_latency = metrics.get('http_request_latency', {})
        uptime = metrics.get('uptime_seconds', 0)
        
        # Get unique exchanges from all metric sources
        all_exchanges = set()
        for key in active_connections.keys():
            exchange = key.split('|')[0].replace('exchange=', '') if '|' in key else 'unknown'
            all_exchanges.add(exchange)
        
        for exchange in all_exchanges:
            # Calculate metrics
            is_connected = any(
                exchange in k and v > 0
                for k, v in active_connections.items()
            )
            
            # Get latency
            latency_key = f"exchange={exchange}"
            latency_snapshot = None
            for key, snapshot in request_latency.items():
                if exchange in key and snapshot:
                    latency_snapshot = snapshot
                    break
            
            avg_latency = latency_snapshot.avg_latency_ms if latency_snapshot else 0
            current_latency = latency_snapshot.p50 if latency_snapshot else None
            
            # Calculate error rate
            total_errors = sum(
                v for k, v in request_errors.items() if exchange in k
            )
            total_requests = sum(
                v for k, v in metrics.get('http_requests', {}).items() if exchange in k
            )
            error_rate = total_errors / max(total_requests, 1)
            
            # Determine health status
            if not is_connected:
                status = HealthStatus.UNHEALTHY
            elif error_rate > self.max_error_rate:
                status = HealthStatus.UNHEALTHY
            elif avg_latency > self.max_latency_threshold_ms:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
            
            # Get last error
            last_error = None
            last_error_time = None
            for key, value in request_errors.items():
                if exchange in key and value > 0:
                    last_error = key
                    last_error_time = time.time()
            
            # Calculate connection success rate
            total_attempts = sum(
                v for k, v in connection_attempts.items() if exchange in k
            )
            successful = sum(
                v for k, v in connection_attempts.items() 
                if exchange in k and 'status=success' in k
            )
            success_rate = successful / max(total_attempts, 1)
            
            self._exchange_health[exchange] = ExchangeHealth(
                exchange=exchange,
                status=status,
                is_connected=is_connected,
                latency_ms=current_latency,
                avg_latency_ms=avg_latency,
                error_rate=error_rate,
                last_error=last_error,
                last_error_time=last_error_time,
                uptime_seconds=uptime,
                connection_success_rate=success_rate,
                circuit_state=self._circuit_states.get(exchange, CircuitState.UNKNOWN)
            )
    
    async def _refresh_rate_limits(self, metrics: Dict[str, Any]) -> None:
        """Refresh rate limit status."""
        rate_limit_hits = metrics.get('rate_limit_hits', {})
        wait_time = metrics.get('rate_limit_wait_time', {})
        
        for exchange in self._exchanges:
            # Get rate limit info if available
            info = self._rate_limit_info.get(exchange, {})
            remaining = info.get('remaining', 100)
            limit = info.get('limit', 100)
            reset_time = info.get('reset_time')
            
            # Calculate usage
            usage = 1.0 - (remaining / max(limit, 1))
            
            # Get hit count
            hit_count = sum(
                v for k, v in rate_limit_hits.items() if exchange in k
            )
            
            # Get avg wait time
            wait_snapshots = [
                s for k, s in wait_time.items() 
                if exchange in k and s is not None
            ]
            avg_wait = sum(s.p50 for s in wait_snapshots) / len(wait_snapshots) if wait_snapshots else 0
            
            self._rate_limits[exchange] = RateLimitStatus(
                exchange=exchange,
                current_usage=usage,
                remaining=remaining,
                limit=limit,
                reset_time=reset_time,
                hit_count=hit_count,
                wait_time_avg=avg_wait
            )
    
    async def _refresh_order_metrics(self, metrics: Dict[str, Any]) -> None:
        """Refresh order metrics."""
        orders_placed = metrics.get('orders_placed', {})
        orders_filled = metrics.get('orders_filled', {})
        orders_cancelled = metrics.get('orders_cancelled', {})
        placement_latency = metrics.get('order_placement_latency', {})
        
        for exchange in self._exchanges:
            # Count orders
            placed = sum(
                v for k, v in orders_placed.items() 
                if exchange in k and 'status=success' in k
            )
            filled = sum(
                v for k, v in orders_filled.items() if exchange in k
            )
            cancelled = sum(
                v for k, v in orders_cancelled.items() if exchange in k
            )
            
            # Calculate fill rate
            fill_rate = filled / max(placed, 1)
            
            # Get latency metrics
            latency_snapshots = [
                s for k, s in placement_latency.items() 
                if exchange in k and s is not None
            ]
            
            if latency_snapshots:
                avg_latency = sum(s.p50 for s in latency_snapshots) / len(latency_snapshots)
                p95 = max(s.p95 for s in latency_snapshots)
                p99 = max(s.p99 for s in latency_snapshots)
            else:
                avg_latency = p95 = p99 = 0
            
            self._order_metrics[exchange] = OrderMetrics(
                exchange=exchange,
                total_placed=placed,
                total_filled=filled,
                total_cancelled=cancelled,
                fill_rate=fill_rate,
                avg_placement_latency_ms=avg_latency * 1000,  # Convert to ms
                p95_placement_latency_ms=p95 * 1000,
                p99_placement_latency_ms=p99 * 1000
            )
    
    async def _refresh_connection_metrics(self, metrics: Dict[str, Any]) -> None:
        """Refresh connection metrics."""
        active_connections = metrics.get('active_connections', {})
        connection_attempts = metrics.get('connection_attempts', {})
        reconnections = metrics.get('reconnections', {})
        ws_received = metrics.get('ws_messages_received', {})
        ws_sent = metrics.get('ws_messages_sent', {})
        ws_errors = metrics.get('ws_errors', {})
        
        for exchange in self._exchanges:
            # Count connections
            active = sum(
                v for k, v in active_connections.items() if exchange in k
            )
            
            # Count attempts
            attempts = sum(
                v for k, v in connection_attempts.items() if exchange in k
            )
            successful = sum(
                v for k, v in connection_attempts.items() 
                if exchange in k and 'status=success' in k
            )
            failed = sum(
                v for k, v in connection_attempts.items() 
                if exchange in k and 'status=failed' in k
            )
            
            # Count reconnections
            recon_count = sum(
                v for k, v in reconnections.items() if exchange in k
            )
            
            # Count WebSocket messages
            sent = sum(
                v for k, v in ws_sent.items() if exchange in k
            )
            received = sum(
                v for k, v in ws_received.items() if exchange in k
            )
            errors = sum(
                v for k, v in ws_errors.items() if exchange in k
            )
            
            success_rate = successful / max(attempts, 1)
            
            self._connection_metrics[exchange] = ConnectionMetrics(
                exchange=exchange,
                active_connections=int(active),
                total_attempts=int(attempts),
                successful_connections=int(successful),
                failed_connections=int(failed),
                reconnections=int(recon_count),
                connection_success_rate=success_rate,
                messages_sent=int(sent),
                messages_received=int(received),
                ws_errors=int(errors)
            )
    
    async def _refresh_trading_summaries(self, metrics: Dict[str, Any]) -> None:
        """Refresh trading summaries."""
        self._trading_summary_cache = {}
        
        for period in ['daily', 'weekly', 'monthly']:
            self._trading_summary_cache[period] = await self._calculate_trading_summary(period)
    
    async def _calculate_trading_summary(self, period: str) -> TradingSummary:
        """Calculate trading summary for a period."""
        now = time.time()
        
        # Calculate time range
        if period == 'daily':
            start_time = now - 86400
        elif period == 'weekly':
            start_time = now - 604800
        elif period == 'monthly':
            start_time = now - 2592000
        else:
            start_time = now - 86400
        
        # Get metrics
        metrics = await self.collector.get_all_metrics()
        trade_count = metrics.get('trade_count', {})
        trade_volume = metrics.get('trade_volume', {})
        pnl = metrics.get('pnl', {})
        
        total_trades = sum(trade_count.values())
        total_volume = sum(trade_volume.values())
        
        # Calculate P&L
        total_pnl = sum(
            v for k, v in pnl.items()
        )
        
        # Estimate win/loss (simplified - in production, track individually)
        win_count = int(total_trades * 0.55)  # Placeholder
        loss_count = total_trades - win_count
        win_rate = win_count / max(total_trades, 1)
        
        return TradingSummary(
            period=period,
            start_time=start_time,
            end_time=now,
            total_trades=total_trades,
            total_volume=total_volume,
            avg_trade_size=total_volume / max(total_trades, 1),
            pnl=total_pnl,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            avg_win=100,  # Placeholder
            avg_loss=-50,  # Placeholder
            profit_factor=1.5,  # Placeholder
            sharpe_ratio=None,
            max_drawdown=None
        )
    
    def _calculate_system_overview(self) -> SystemOverview:
        """Calculate system overview."""
        total_exchanges = len(self._exchange_health)
        healthy = sum(1 for h in self._exchange_health.values() if h.status == HealthStatus.HEALTHY)
        degraded = sum(1 for h in self._exchange_health.values() if h.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for h in self._exchange_health.values() if h.status == HealthStatus.UNHEALTHY)
        
        # Determine overall health
        if unhealthy > 0:
            overall = HealthStatus.UNHEALTHY
        elif degraded > 0:
            overall = HealthStatus.DEGRADED
        elif healthy > 0:
            overall = HealthStatus.HEALTHY
        else:
            overall = HealthStatus.UNKNOWN
        
        # Count open circuit breakers
        open_circuits = sum(
            1 for s in self._circuit_states.values() if s == CircuitState.OPEN
        )
        
        # Sum active connections and requests
        total_connections = sum(
            c.active_connections for c in self._connection_metrics.values()
        )
        
        return SystemOverview(
            uptime_seconds=time.time() - self.collector._started_at,
            total_exchanges=total_exchanges,
            healthy_exchanges=healthy,
            degraded_exchanges=degraded,
            unhealthy_exchanges=unhealthy,
            total_active_connections=total_connections,
            total_active_requests=0,  # Would need to aggregate
            circuit_breakers_open=open_circuits,
            overall_health=overall
        )


# Global dashboard instance
dashboard: Optional[DashboardData] = None


def init_dashboard(
    collector: MetricsCollector,
    **kwargs
) -> DashboardData:
    """Initialize global dashboard."""
    global dashboard
    dashboard = DashboardData(collector, **kwargs)
    return dashboard


def get_dashboard() -> Optional[DashboardData]:
    """Get global dashboard."""
    return dashboard
