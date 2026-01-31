"""Health monitoring and circuit breaker for exchange adapters."""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Set
from collections import deque
from datetime import datetime
import logging

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException


logger = logging.getLogger(__name__)


class ConnectionStatus(str, Enum):
    """Connection status enumeration."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    CIRCUIT_OPEN = "circuit_open"


class CircuitState(str, Enum):
    """Circuit breaker state enumeration."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class HealthMetrics:
    """Health metrics for an exchange connection."""
    exchange: str
    status: ConnectionStatus
    last_ping_time: Optional[float] = None
    last_pong_time: Optional[float] = None
    latency_ms: Optional[float] = None
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    connection_attempts: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    reconnections: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None
    messages_received: int = 0
    messages_sent: int = 0
    uptime_seconds: float = 0.0
    connected_since: Optional[float] = None
    
    # Latency history (keep last 100 measurements)
    latency_history: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def update_latency(self, latency_ms: float) -> None:
        """Update latency metrics."""
        self.latency_ms = latency_ms
        self.latency_history.append(latency_ms)
        
        # Update statistics
        self.avg_latency_ms = sum(self.latency_history) / len(self.latency_history)
        self.max_latency_ms = max(self.latency_history)
        self.min_latency_ms = min(self.latency_history)
    
    def record_error(self, error: str) -> None:
        """Record an error."""
        self.errors += 1
        self.last_error = error
        self.last_error_time = time.time()
    
    def record_connection_success(self) -> None:
        """Record successful connection."""
        self.connection_attempts += 1
        self.successful_connections += 1
        self.connected_since = time.time()
        self.status = ConnectionStatus.CONNECTED
    
    def record_connection_failure(self, error: str) -> None:
        """Record connection failure."""
        self.connection_attempts += 1
        self.failed_connections += 1
        self.record_error(error)
        self.status = ConnectionStatus.ERROR
    
    def record_disconnection(self) -> None:
        """Record disconnection."""
        if self.connected_since:
            self.uptime_seconds += time.time() - self.connected_since
        self.connected_since = None
        self.status = ConnectionStatus.DISCONNECTED
    
    def record_reconnection(self) -> None:
        """Record reconnection attempt."""
        self.reconnections += 1
        self.status = ConnectionStatus.RECONNECTING
    
    @property
    def connection_success_rate(self) -> float:
        """Calculate connection success rate."""
        if self.connection_attempts == 0:
            return 1.0
        return self.successful_connections / self.connection_attempts
    
    @property
    def is_healthy(self) -> bool:
        """Check if connection is healthy."""
        return (
            self.status == ConnectionStatus.CONNECTED and
            self.connection_success_rate >= 0.8 and
            (self.latency_ms is None or self.latency_ms < 5000)  # < 5s latency
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'exchange': self.exchange,
            'status': self.status.value,
            'latency_ms': self.latency_ms,
            'avg_latency_ms': self.avg_latency_ms,
            'max_latency_ms': self.max_latency_ms,
            'min_latency_ms': self.min_latency_ms if self.min_latency_ms != float('inf') else None,
            'connection_attempts': self.connection_attempts,
            'successful_connections': self.successful_connections,
            'failed_connections': self.failed_connections,
            'reconnections': self.reconnections,
            'errors': self.errors,
            'last_error': self.last_error,
            'last_error_time': self.last_error_time,
            'messages_received': self.messages_received,
            'messages_sent': self.messages_sent,
            'uptime_seconds': self.uptime_seconds,
            'connected_since': self.connected_since,
            'is_healthy': self.is_healthy,
            'connection_success_rate': self.connection_success_rate
        }


class CircuitBreaker:
    """Circuit breaker pattern implementation for exchange connections.
    
    The circuit breaker prevents cascading failures by temporarily rejecting
    requests when an exchange is experiencing problems.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failing, requests are rejected immediately
    - HALF_OPEN: Testing if service recovered, limited requests allowed
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2
    ):
        """Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying recovery
            half_open_max_calls: Max calls allowed in half-open state
            success_threshold: Successes needed to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
        self._lock = asyncio.Lock()
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing)."""
        return self.state == CircuitState.OPEN
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal)."""
        return self.state == CircuitState.CLOSED
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing)."""
        return self.state == CircuitState.HALF_OPEN
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function with circuit breaker protection.
        
        Args:
            func: Async function to call
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpen: If circuit is open
            Exception: Original exception from function
        """
        async with self._lock:
            await self._update_state()
            
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpen("Circuit breaker is open")
            
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpen("Circuit breaker half-open limit reached")
                self.half_open_calls += 1
        
        # Execute outside lock
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure()
            raise
    
    async def _update_state(self) -> None:
        """Update circuit state based on time and failures."""
        if self.state == CircuitState.OPEN:
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) >= self.recovery_timeout:
                logger.info("Circuit breaker entering half-open state")
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                self.success_count = 0
    
    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    logger.info("Circuit breaker closing (recovered)")
                    self._reset()
            else:
                self.failure_count = max(0, self.failure_count - 1)
    
    async def record_failure(self) -> None:
        """Record a failed call."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                logger.warning("Circuit breaker opening (failure in half-open)")
                self.state = CircuitState.OPEN
            elif self.failure_count >= self.failure_threshold:
                logger.warning(f"Circuit breaker opening ({self.failure_count} failures)")
                self.state = CircuitState.OPEN
    
    def _reset(self) -> None:
        """Reset circuit breaker to closed state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_failure_time = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'failure_threshold': self.failure_threshold,
            'recovery_timeout': self.recovery_timeout,
            'is_open': self.is_open,
            'is_closed': self.is_closed,
            'is_half_open': self.is_half_open,
            'last_failure_time': self.last_failure_time
        }


class CircuitBreakerOpen(Exception):
    """Exception raised when circuit breaker is open."""
    pass


class ExchangeHealthMonitor:
    """Health monitor for exchange connections.
    
    Features:
    - Connection status tracking per exchange
    - Latency measurement (ping/pong)
    - Automatic reconnection on disconnect
    - Circuit breaker pattern for failing exchanges
    - Health status reporting
    """
    
    def __init__(
        self,
        ping_interval: float = 30.0,
        pong_timeout: float = 10.0,
        reconnect_delay: float = 5.0,
        max_reconnect_delay: float = 60.0,
        enable_circuit_breaker: bool = True
    ):
        """Initialize health monitor.
        
        Args:
            ping_interval: Seconds between ping messages
            pong_timeout: Seconds to wait for pong response
            reconnect_delay: Initial delay between reconnection attempts
            max_reconnect_delay: Maximum reconnection delay
            enable_circuit_breaker: Whether to use circuit breaker
        """
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.enable_circuit_breaker = enable_circuit_breaker
        
        # Health metrics per exchange
        self.metrics: Dict[str, HealthMetrics] = {}
        
        # Circuit breakers per exchange
        self.circuits: Dict[str, CircuitBreaker] = {}
        
        # Connection handlers
        self.connect_handlers: Dict[str, Callable] = {}
        self.disconnect_handlers: Dict[str, Callable] = {}
        
        # Active connections
        self.connections: Dict[str, Any] = {}
        
        # Ping/pong tracking
        self._pending_pings: Dict[str, float] = {}
        
        # Background tasks
        self._tasks: Set[asyncio.Task] = set()
        self._running = False
    
    def register_exchange(
        self,
        exchange: str,
        connect_handler: Callable,
        disconnect_handler: Optional[Callable] = None
    ) -> None:
        """Register an exchange for health monitoring.
        
        Args:
            exchange: Exchange name
            connect_handler: Async function to establish connection
            disconnect_handler: Optional async function to close connection
        """
        self.metrics[exchange] = HealthMetrics(exchange=exchange, status=ConnectionStatus.DISCONNECTED)
        self.connect_handlers[exchange] = connect_handler
        if disconnect_handler:
            self.disconnect_handlers[exchange] = disconnect_handler
        
        if self.enable_circuit_breaker:
            self.circuits[exchange] = CircuitBreaker()
        
        logger.info(f"Registered exchange for health monitoring: {exchange}")
    
    async def start(self) -> None:
        """Start health monitoring."""
        self._running = True
        
        # Start ping loop for all registered exchanges
        for exchange in self.metrics:
            task = asyncio.create_task(self._ping_loop(exchange))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        
        logger.info("Health monitor started")
    
    async def stop(self) -> None:
        """Stop health monitoring."""
        self._running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        logger.info("Health monitor stopped")
    
    async def connect(self, exchange: str) -> bool:
        """Connect to an exchange with health monitoring.
        
        Args:
            exchange: Exchange name
            
        Returns:
            True if connection successful
        """
        if exchange not in self.metrics:
            raise ValueError(f"Exchange not registered: {exchange}")
        
        metrics = self.metrics[exchange]
        
        # Check circuit breaker
        if self.enable_circuit_breaker and exchange in self.circuits:
            if self.circuits[exchange].is_open:
                logger.warning(f"Circuit breaker open for {exchange}, skipping connection")
                metrics.status = ConnectionStatus.CIRCUIT_OPEN
                return False
        
        metrics.status = ConnectionStatus.CONNECTING
        
        try:
            start_time = time.time()
            
            # Use circuit breaker if enabled
            if self.enable_circuit_breaker and exchange in self.circuits:
                connection = await self.circuits[exchange].call(
                    self.connect_handlers[exchange]
                )
            else:
                connection = await self.connect_handlers[exchange]()
            
            # Record success
            latency_ms = (time.time() - start_time) * 1000
            metrics.update_latency(latency_ms)
            metrics.record_connection_success()
            
            self.connections[exchange] = connection
            
            logger.info(f"Connected to {exchange} (latency: {latency_ms:.2f}ms)")
            return True
            
        except CircuitBreakerOpen:
            metrics.status = ConnectionStatus.CIRCUIT_OPEN
            return False
        except Exception as e:
            error_msg = str(e)
            metrics.record_connection_failure(error_msg)
            
            if self.enable_circuit_breaker and exchange in self.circuits:
                await self.circuits[exchange].record_failure()
            
            logger.error(f"Failed to connect to {exchange}: {error_msg}")
            return False
    
    async def disconnect(self, exchange: str) -> None:
        """Disconnect from an exchange.
        
        Args:
            exchange: Exchange name
        """
        if exchange not in self.metrics:
            return
        
        metrics = self.metrics[exchange]
        metrics.record_disconnection()
        
        # Call disconnect handler if available
        if exchange in self.disconnect_handlers:
            try:
                await self.disconnect_handlers[exchange]()
            except Exception as e:
                logger.error(f"Error disconnecting from {exchange}: {e}")
        
        # Remove connection
        if exchange in self.connections:
            del self.connections[exchange]
        
        logger.info(f"Disconnected from {exchange}")
    
    async def _ping_loop(self, exchange: str) -> None:
        """Background task to send ping messages and monitor latency.
        
        Args:
            exchange: Exchange name
        """
        while self._running:
            try:
                if exchange in self.connections:
                    await self._send_ping(exchange)
                
                await asyncio.sleep(self.ping_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ping loop for {exchange}: {e}")
                await asyncio.sleep(self.ping_interval)
    
    async def _send_ping(self, exchange: str) -> None:
        """Send ping and measure latency.
        
        Args:
            exchange: Exchange name
        """
        metrics = self.metrics[exchange]
        connection = self.connections.get(exchange)
        
        if not connection:
            return
        
        ping_time = time.time()
        metrics.last_ping_time = ping_time
        self._pending_pings[exchange] = ping_time
        
        try:
            # Send ping based on connection type
            if isinstance(connection, websockets.WebSocketClientProtocol):
                await connection.ping()
                # Wait for pong (websockets handles this internally)
                latency_ms = (time.time() - ping_time) * 1000
            elif hasattr(connection, 'ping'):
                await connection.ping()
                latency_ms = (time.time() - ping_time) * 1000
            else:
                # For HTTP clients, make a lightweight request
                latency_ms = await self._http_ping(connection)
            
            metrics.update_latency(latency_ms)
            metrics.last_pong_time = time.time()
            
            del self._pending_pings[exchange]
            
        except asyncio.TimeoutError:
            logger.warning(f"Ping timeout for {exchange}")
            metrics.record_error("Ping timeout")
            await self._handle_connection_issue(exchange)
        except Exception as e:
            logger.error(f"Ping failed for {exchange}: {e}")
            metrics.record_error(f"Ping failed: {str(e)}")
            await self._handle_connection_issue(exchange)
    
    async def _http_ping(self, connection: Any) -> float:
        """Measure latency for HTTP connection.
        
        Args:
            connection: HTTP client connection
            
        Returns:
            Latency in milliseconds
        """
        start = time.time()
        
        if isinstance(connection, httpx.AsyncClient):
            # Make a simple request (e.g., get server time)
            await connection.get('/api/v3/ping')
        
        return (time.time() - start) * 1000
    
    async def _handle_connection_issue(self, exchange: str) -> None:
        """Handle connection issue by triggering reconnection.
        
        Args:
            exchange: Exchange name
        """
        metrics = self.metrics[exchange]
        
        if metrics.status == ConnectionStatus.CONNECTED:
            logger.warning(f"Connection issue detected for {exchange}, reconnecting...")
            await self._reconnect(exchange)
    
    async def _reconnect(self, exchange: str) -> bool:
        """Reconnect to an exchange with exponential backoff.
        
        Args:
            exchange: Exchange name
            
        Returns:
            True if reconnection successful
        """
        metrics = self.metrics[exchange]
        metrics.record_reconnection()
        
        # Disconnect first
        await self.disconnect(exchange)
        
        # Calculate delay with exponential backoff
        attempt = metrics.reconnections
        delay = min(self.reconnect_delay * (2 ** (attempt - 1)), self.max_reconnect_delay)
        
        logger.info(f"Reconnecting to {exchange} in {delay:.1f}s (attempt {attempt})")
        await asyncio.sleep(delay)
        
        # Attempt connection
        return await self.connect(exchange)
    
    def get_health_status(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get health status for one or all exchanges.
        
        Args:
            exchange: Specific exchange name, or None for all
            
        Returns:
            Health status dictionary
        """
        if exchange:
            if exchange not in self.metrics:
                raise ValueError(f"Exchange not registered: {exchange}")
            return self.metrics[exchange].to_dict()
        
        return {
            'exchanges': {
                name: metrics.to_dict() 
                for name, metrics in self.metrics.items()
            },
            'summary': {
                'total': len(self.metrics),
                'connected': sum(1 for m in self.metrics.values() 
                               if m.status == ConnectionStatus.CONNECTED),
                'healthy': sum(1 for m in self.metrics.values() if m.is_healthy)
            }
        }
    
    def get_circuit_status(self, exchange: Optional[str] = None) -> Dict[str, Any]:
        """Get circuit breaker status.
        
        Args:
            exchange: Specific exchange name, or None for all
            
        Returns:
            Circuit breaker status dictionary
        """
        if not self.enable_circuit_breaker:
            return {'enabled': False}
        
        if exchange:
            if exchange not in self.circuits:
                raise ValueError(f"Exchange not registered: {exchange}")
            return self.circuits[exchange].to_dict()
        
        return {
            'enabled': True,
            'exchanges': {
                name: circuit.to_dict()
                for name, circuit in self.circuits.items()
            }
        }
    
    def is_healthy(self, exchange: str) -> bool:
        """Check if an exchange connection is healthy.
        
        Args:
            exchange: Exchange name
            
        Returns:
            True if healthy
        """
        if exchange not in self.metrics:
            return False
        return self.metrics[exchange].is_healthy
    
    def is_connected(self, exchange: str) -> bool:
        """Check if an exchange is connected.
        
        Args:
            exchange: Exchange name
            
        Returns:
            True if connected
        """
        if exchange not in self.metrics:
            return False
        return self.metrics[exchange].status == ConnectionStatus.CONNECTED
    
    async def record_message_received(self, exchange: str) -> None:
        """Record a message received from an exchange.
        
        Args:
            exchange: Exchange name
        """
        if exchange in self.metrics:
            self.metrics[exchange].messages_received += 1
    
    async def record_message_sent(self, exchange: str) -> None:
        """Record a message sent to an exchange.
        
        Args:
            exchange: Exchange name
        """
        if exchange in self.metrics:
            self.metrics[exchange].messages_sent += 1