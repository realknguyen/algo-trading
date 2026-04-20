"""Base exchange adapter with async HTTP client and comprehensive error handling.

This module provides the foundation for all exchange adapters, including:
- Abstract base class defining the exchange interface
- Async HTTP client with connection pooling
- Rate limiting per exchange
- Retry logic with exponential backoff
- WebSocket support hooks
- Comprehensive error handling
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import (
    Optional,
    Dict,
    Any,
    List,
    Callable,
    Union,
    AsyncIterator,
    TypeVar,
    Generic,
    Coroutine,
)
from urllib.parse import urlencode

import httpx
from aiolimiter import AsyncLimiter

from src.adapters.exceptions import (
    ExchangeError,
    ExchangeConnectionError,
    AuthenticationError,
    RateLimitError,
    OrderError,
    InsufficientFundsError,
    InvalidSymbolError,
    MarketClosedError,
    DataValidationError,
)
from src.adapters.auth import AuthConfig, RequestSigner, create_signer


class OrderType(str, Enum):
    """Order type enumeration."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    TRAILING_STOP = "trailing_stop"
    STOP_LOSS_LIMIT = "stop_loss_limit"


class OrderSide(str, Enum):
    """Order side enumeration."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """Order status enumeration."""

    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    """Time in force enumeration."""

    GTC = "GTC"  # Good Till Cancelled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    DAY = "DAY"  # Day order
    GTD = "GTD"  # Good Till Date


@dataclass
class Order:
    """Order dataclass representing a trading order.

    Attributes:
        symbol: Trading pair symbol (e.g., "BTCUSD", "ETH-USD")
        side: Buy or sell
        order_type: Type of order (market, limit, etc.)
        quantity: Amount to trade
        price: Limit price (required for limit orders)
        stop_price: Trigger price for stop orders
        time_in_force: How long the order remains active
        client_order_id: Client-generated order ID
        order_id: Exchange-generated order ID (filled after placement)
        status: Current order status
        filled_quantity: Amount already filled
        avg_fill_price: Average fill price
        commission: Trading fee paid
        created_at: Order creation timestamp
    """

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    client_order_id: Optional[str] = None

    # Response fields (filled by exchange)
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    avg_fill_price: Optional[Decimal] = None
    commission: Optional[Decimal] = None
    created_at: Optional[float] = None

    @property
    def remaining_quantity(self) -> Decimal:
        """Get remaining unfilled quantity."""
        return self.quantity - self.filled_quantity

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED or self.filled_quantity >= self.quantity

    @property
    def is_active(self) -> bool:
        """Check if order is still active (not finalized)."""
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


@dataclass
class Position:
    """Position dataclass representing an open position.

    Attributes:
        symbol: Trading pair symbol
        quantity: Position size (positive for long, negative for short)
        avg_entry_price: Average entry price
        current_price: Current market price
        unrealized_pnl: Unrealized profit/loss
        realized_pnl: Realized profit/loss
        leverage: Position leverage (if applicable)
        margin_mode: Cross or isolated margin
    """

    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    leverage: Decimal = field(default_factory=lambda: Decimal("1"))
    margin_mode: Optional[str] = None

    @property
    def market_value(self) -> Decimal:
        """Calculate current market value of position."""
        return abs(self.quantity) * self.current_price

    @property
    def side(self) -> OrderSide:
        """Determine if position is long or short."""
        return OrderSide.BUY if self.quantity > 0 else OrderSide.SELL


@dataclass
class Ticker:
    """Market ticker dataclass.

    Attributes:
        symbol: Trading pair symbol
        bid: Best bid price
        ask: Best ask price
        last: Last traded price
        volume: Trading volume
        timestamp: Data timestamp
        high_24h: 24-hour high
        low_24h: 24-hour low
    """

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    timestamp: float
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None

    @property
    def spread(self) -> Decimal:
        """Calculate bid-ask spread."""
        return self.ask - self.bid

    @property
    def spread_pct(self) -> Decimal:
        """Calculate bid-ask spread as percentage."""
        if self.ask == 0:
            return Decimal("0")
        return ((self.ask - self.bid) / self.ask) * 100


@dataclass
class Balance:
    """Account balance dataclass.

    Attributes:
        asset: Asset/currency symbol
        free: Available for trading
        locked: Locked in orders or other operations
        total: Total balance (free + locked)
    """

    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal


@dataclass
class Candle:
    """OHLCV candle dataclass.

    Attributes:
        timestamp: Candle open time (Unix timestamp)
        open: Opening price
        high: Highest price
        low: Lowest price
        close: Closing price
        volume: Trading volume
    """

    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class OrderBook:
    """Order book dataclass.

    Attributes:
        symbol: Trading pair symbol
        bids: List of [price, quantity] bids (sorted best to worst)
        asks: List of [price, quantity] asks (sorted best to worst)
        timestamp: Data timestamp
        sequence: Exchange sequence number (if available)
    """

    symbol: str
    bids: List[List[Decimal]]  # [[price, quantity], ...]
    asks: List[List[Decimal]]  # [[price, quantity], ...]
    timestamp: float
    sequence: Optional[int] = None

    @property
    def best_bid(self) -> Optional[Decimal]:
        """Get best bid price."""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        """Get best ask price."""
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Calculate mid price."""
        best_bid = self.best_bid
        best_ask = self.best_ask
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return None


@dataclass
class AccountInfo:
    """Account information dataclass.

    Attributes:
        account_id: Unique account identifier
        account_type: Account type (spot, margin, futures, etc.)
        permissions: List of account permissions
        created_at: Account creation timestamp
    """

    account_id: str
    account_type: str
    permissions: List[str] = field(default_factory=list)
    created_at: Optional[float] = None


class RetryConfig:
    """Configuration for retry logic.

    Attributes:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        exponential_base: Base for exponential backoff
        retry_on_status: HTTP status codes that trigger retry
        retry_on_exceptions: Exception types that trigger retry
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        retry_on_status: Optional[List[int]] = None,
        retry_on_exceptions: Optional[List[type]] = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.retry_on_status = retry_on_status or [429, 500, 502, 503, 504]
        self.retry_on_exceptions = retry_on_exceptions or [
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.ReadError,
            httpx.WriteError,
        ]

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt."""
        delay = self.base_delay * (self.exponential_base**attempt)
        return min(delay, self.max_delay)


class BaseExchangeAdapter(ABC):
    """Abstract base class for exchange adapters.

    This class provides the foundation for implementing exchange-specific
    adapters. It handles:
    - Async HTTP client with connection pooling
    - Rate limiting per exchange
    - Authentication via pluggable signers
    - Retry logic with exponential backoff
    - Error handling and translation
    - WebSocket support hooks

    Subclasses must implement all abstract methods to provide exchange-specific
    functionality.

    Example:
        >>> class MyExchangeAdapter(BaseExchangeAdapter):
        ...     async def connect(self) -> bool:
        ...         # Implementation
        ...         pass
        ...
        >>> async with adapter:
        ...     account = await adapter.get_account()
        ...     await adapter.place_order(order)

    Attributes:
        api_key: API key for authentication
        api_secret: API secret for authentication
        base_url: Base URL for REST API
        is_connected: Connection status
        signer: Request signer instance
        rate_limiter: Async rate limiter
        client: HTTPX async client
    """

    # Exchange identification
    exchange_name: str = "base"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        rate_limit_per_second: float = 10.0,
        sandbox: bool = True,
        auth_type: str = "hmac",
        passphrase: Optional[str] = None,
        private_key: Optional[Union[str, bytes]] = None,
        retry_config: Optional[RetryConfig] = None,
        **kwargs,
    ):
        """Initialize the exchange adapter.

        Args:
            api_key: API key for authentication
            api_secret: API secret for HMAC signing
            base_url: Base URL for REST API
            rate_limit_per_second: Maximum requests per second
            sandbox: Whether to use sandbox/testnet
            auth_type: Authentication type ("hmac", "rsa", "ed25519", etc.)
            passphrase: Optional passphrase (for exchanges like Coinbase)
            private_key: Private key for RSA/Ed25519 signing
            retry_config: Custom retry configuration
            **kwargs: Additional exchange-specific parameters
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._sandbox = sandbox
        self._retry_config = retry_config or RetryConfig()

        # Initialize authentication
        auth_config = AuthConfig(
            api_key=api_key, api_secret=api_secret, passphrase=passphrase, private_key=private_key
        )

        try:
            self._signer = create_signer(auth_type, auth_config)
        except (ValueError, ImportError) as e:
            raise AuthenticationError(
                f"Failed to initialize signer: {e}", exchange=self.exchange_name
            )

        # Rate limiter
        self._rate_limiter = AsyncLimiter(rate_limit_per_second, time_period=1.0)

        # HTTP client
        limits = httpx.Limits(
            max_connections=kwargs.get("max_connections", 100),
            max_keepalive_connections=kwargs.get("max_keepalive_connections", 20),
        )
        timeout = httpx.Timeout(
            kwargs.get("timeout", 30.0), connect=kwargs.get("connect_timeout", 10.0)
        )

        self._client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            base_url=self._base_url,
            headers={
                "User-Agent": kwargs.get(
                    "user_agent", f"AlgoTradingBot/1.0 ({self.exchange_name})"
                ),
                "Accept": "application/json",
            },
        )

        # Connection status
        self._connected = False
        self._connection_info: Dict[str, Any] = {}

        # WebSocket configuration
        self._ws_url = kwargs.get("ws_url")
        self._ws_client: Optional[Any] = None
        self._market_data_callbacks: Dict[str, List[Callable]] = {}

    # Properties

    @property
    def api_key(self) -> str:
        """API key for authentication."""
        return self._api_key

    @property
    def api_secret(self) -> str:
        """API secret for authentication."""
        return self._api_secret

    @property
    def base_url(self) -> str:
        """Base URL for REST API."""
        return self._base_url

    @property
    def is_connected(self) -> bool:
        """Check if adapter is connected to the exchange."""
        return self._connected

    @property
    def is_sandbox(self) -> bool:
        """Check if using sandbox/testnet environment."""
        return self._sandbox

    @property
    def signer(self) -> RequestSigner:
        """Request signer instance."""
        return self._signer

    # Context Managers

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()

    # Connection Management

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to exchange and validate credentials.

        This method should:
        1. Test connectivity to the exchange
        2. Validate API credentials
        3. Synchronize clock if needed
        4. Set _connected to True on success

        Returns:
            True if connection successful, False otherwise

        Raises:
            ExchangeConnectionError: If connection fails
            AuthenticationError: If credentials are invalid
        """
        pass

    async def disconnect(self) -> None:
        """Disconnect from exchange and cleanup resources.

        This method:
        1. Closes WebSocket connections if active
        2. Closes HTTP client
        3. Sets _connected to False
        """
        try:
            if self._ws_client:
                # Close WebSocket (implementation depends on client)
                pass

            await self._client.aclose()
            self._connected = False
        except Exception as e:
            # Log but don't raise on cleanup errors
            pass

    # Abstract Methods - Account

    @abstractmethod
    async def get_account(self) -> AccountInfo:
        """Get account information.

        Returns:
            Account information including ID, type, and permissions

        Raises:
            AuthenticationError: If authentication fails
            ExchangeConnectionError: If connection fails
        """
        pass

    @abstractmethod
    async def get_balances(self) -> List[Balance]:
        """Get account balances for all assets.

        Returns:
            List of balances for each asset
        """
        pass

    # Abstract Methods - Trading

    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """Place a new order on the exchange.

        Args:
            order: Order to place

        Returns:
            Updated order with exchange-generated order_id and status

        Raises:
            OrderError: If order placement fails
            InsufficientFundsError: If not enough balance
            InvalidSymbolError: If symbol is invalid
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancel an existing order.

        Args:
            order_id: Exchange-generated order ID
            symbol: Trading symbol (required by some exchanges)

        Returns:
            True if cancellation successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str, symbol: Optional[str] = None) -> Order:
        """Get order status by ID.

        Args:
            order_id: Exchange-generated order ID
            symbol: Trading symbol (required by some exchanges)

        Returns:
            Order with current status
        """
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of open orders
        """
        pass

    # Abstract Methods - Positions

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get current positions.

        Returns:
            List of current positions
        """
        pass

    # Abstract Methods - Market Data

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Current ticker data
        """
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """Get order book for symbol.

        Args:
            symbol: Trading pair symbol
            limit: Number of levels to retrieve

        Returns:
            Order book data
        """
        pass

    @abstractmethod
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """Get historical OHLCV data.

        Args:
            symbol: Trading pair symbol
            interval: Candle interval (e.g., "1m", "5m", "1h", "1d")
            start_time: Start timestamp (Unix)
            end_time: End timestamp (Unix)
            limit: Maximum number of candles

        Returns:
            List of OHLCV candles
        """
        pass

    # Abstract Methods - WebSocket

    @abstractmethod
    async def subscribe_market_data(
        self, symbols: List[str], channels: List[str], callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to real-time market data via WebSocket.

        Args:
            symbols: List of trading symbols to subscribe to
            channels: Data channels (e.g., ["ticker", "trades", "orderbook"])
            callback: Function to call with data updates

        Raises:
            WebSocketError: If subscription fails
        """
        pass

    async def unsubscribe_market_data(
        self, symbols: Optional[List[str]] = None, channels: Optional[List[str]] = None
    ) -> None:
        """Unsubscribe from market data.

        Args:
            symbols: Symbols to unsubscribe (None = all)
            channels: Channels to unsubscribe (None = all)
        """
        # Default implementation - override for specific exchanges
        pass

    # HTTP Request Helpers

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request with rate limiting and retry logic.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint
            params: Query parameters
            data: Request body data
            signed: Whether to sign the request
            headers: Additional headers

        Returns:
            JSON response as dictionary

        Raises:
            ExchangeConnectionError: On connection failures
            AuthenticationError: On authentication failures
            RateLimitError: On rate limiting
        """
        url = f"{self._base_url}{endpoint}"
        request_headers = dict(headers) if headers else {}

        if signed:
            auth_headers = self._signer.sign_request(method, endpoint, params, data)
            request_headers.update(auth_headers)

        last_exception = None

        for attempt in range(self._retry_config.max_retries):
            async with self._rate_limiter:
                try:
                    response = await self._do_request(method, url, params, data, request_headers)
                    return response

                except (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.ReadError,
                    httpx.WriteError,
                ) as e:
                    last_exception = e
                    if attempt < self._retry_config.max_retries - 1:
                        delay = self._retry_config.get_delay(attempt)
                        await asyncio.sleep(delay)
                        continue
                    raise ExchangeConnectionError(
                        f"Request failed after {attempt + 1} attempts: {e}",
                        exchange=self.exchange_name,
                    )

                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code

                    # Handle rate limiting
                    if status_code == 429:
                        retry_after = self._extract_retry_after(e.response)
                        raise RateLimitError(
                            "Rate limit exceeded",
                            exchange=self.exchange_name,
                            retry_after=retry_after,
                        )

                    # Handle authentication errors
                    if status_code in (401, 403):
                        raise AuthenticationError(
                            f"Authentication failed: {e.response.text}", exchange=self.exchange_name
                        )

                    # Retry on server errors
                    if status_code in self._retry_config.retry_on_status:
                        if attempt < self._retry_config.max_retries - 1:
                            delay = self._retry_config.get_delay(attempt)
                            await asyncio.sleep(delay)
                            continue

                    # Raise specific errors based on status
                    self._handle_http_error(status_code, e.response)

        # Should not reach here, but just in case
        raise ExchangeError(
            f"Request failed after {self._retry_config.max_retries} attempts",
            exchange=self.exchange_name,
        )

    async def _do_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Dict[str, Any]],
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """Execute the actual HTTP request.

        Override this method for exchange-specific request handling.
        """
        method_upper = method.upper()

        if method_upper == "GET":
            response = await self._client.get(url, params=params, headers=headers)
        elif method_upper == "POST":
            response = await self._client.post(url, json=data, headers=headers)
        elif method_upper == "PUT":
            response = await self._client.put(url, json=data, headers=headers)
        elif method_upper == "DELETE":
            response = await self._client.delete(url, params=params, headers=headers)
        elif method_upper == "PATCH":
            response = await self._client.patch(url, json=data, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()

        # Return empty dict for 204 No Content
        if response.status_code == 204:
            return {}

        return response.json()

    def _extract_retry_after(self, response: httpx.Response) -> float:
        """Extract retry-after header value."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return 60.0  # Default 60 seconds

    def _handle_http_error(self, status_code: int, response: httpx.Response) -> None:
        """Handle HTTP errors and raise appropriate exceptions."""
        error_text = response.text

        try:
            error_data = response.json()
            error_message = error_data.get("message") or error_data.get("msg") or error_text
        except json.JSONDecodeError:
            error_message = error_text

        if status_code == 400:
            raise OrderError(
                f"Bad request: {error_message}",
                exchange=self.exchange_name,
                response_data={"status_code": status_code},
            )
        elif status_code == 404:
            raise InvalidSymbolError(
                f"Resource not found: {error_message}", exchange=self.exchange_name
            )
        elif status_code == 409:
            raise OrderError(f"Conflict: {error_message}", exchange=self.exchange_name)
        else:
            raise ExchangeError(
                f"HTTP {status_code}: {error_message}",
                exchange=self.exchange_name,
                error_code=str(status_code),
                response_data={"body": error_text},
            )

    # Error Handling Hooks

    def _translate_error(self, error: Exception, context: Dict[str, Any]) -> ExchangeError:
        """Translate exchange-specific errors to standard exceptions.

        Override this method to provide exchange-specific error translation.

        Args:
            error: Original exception
            context: Additional context about the operation

        Returns:
            Translated ExchangeError
        """
        if isinstance(error, ExchangeError):
            return error

        return ExchangeError(str(error), exchange=self.exchange_name, response_data=context)

    def _on_retry(self, attempt: int, error: Exception, delay: float) -> None:
        """Hook called when a retry is attempted.

        Override to implement custom retry logging or metrics.

        Args:
            attempt: Retry attempt number (0-indexed)
            error: Exception that caused the retry
            delay: Seconds before next attempt
        """
        pass

    def _on_rate_limit(self, retry_after: float) -> None:
        """Hook called when rate limit is hit.

        Override to implement custom rate limit handling.

        Args:
            retry_after: Seconds to wait before retry
        """
        pass


# Type alias for exchange adapters
ExchangeAdapterType = TypeVar("ExchangeAdapterType", bound=BaseExchangeAdapter)
