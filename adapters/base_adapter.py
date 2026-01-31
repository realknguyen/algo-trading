"""Base exchange adapter with async HTTP client and rate limiting."""

import asyncio
import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Callable
from urllib.parse import urlencode

import httpx
from aiolimiter import AsyncLimiter

from logging.log_config import TradingLogger


class OrderType(str, Enum):
    """Order type enumeration."""
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"


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


@dataclass
class Order:
    """Order dataclass."""
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


@dataclass
class Position:
    """Position dataclass."""
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class Ticker:
    """Market ticker dataclass."""
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    timestamp: float


@dataclass
class Balance:
    """Account balance dataclass."""
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal


class BaseExchangeAdapter(ABC):
    """Abstract base class for exchange adapters.
    
    Provides:
    - Async HTTP client with connection pooling
    - Rate limiting per exchange
    - Authentication handling
    - Retry logic with exponential backoff
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        rate_limit_per_second: float = 10.0,
        sandbox: bool = True,
        **kwargs
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip('/')
        self.sandbox = sandbox
        self.logger = TradingLogger(self.__class__.__name__)
        
        # Rate limiter
        self.rate_limiter = AsyncLimiter(rate_limit_per_second, time_period=1.0)
        
        # HTTP client
        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20
        )
        timeout = httpx.Timeout(30.0, connect=10.0)
        
        self.client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            base_url=self.base_url
        )
        
        # Connection status
        self._connected = False
        
        # WebSocket (optional)
        self.ws_url = kwargs.get('ws_url')
        self.ws_client = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
    
    async def connect(self) -> bool:
        """Connect to exchange and validate credentials."""
        try:
            await self._authenticate()
            self._connected = True
            self.logger.logger.info(f"Connected to {self.__class__.__name__}")
            return True
        except Exception as e:
            self.logger.error("connection", f"Failed to connect: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from exchange."""
        await self.client.aclose()
        self._connected = False
        self.logger.logger.info(f"Disconnected from {self.__class__.__name__}")
    
    @abstractmethod
    async def _authenticate(self) -> None:
        """Authenticate with the exchange."""
        pass
    
    @abstractmethod
    def _sign_request(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """Sign request with API credentials."""
        pass
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        retries: int = 3
    ) -> Dict[str, Any]:
        """Make HTTP request with rate limiting and retry logic.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            params: Query parameters
            data: Request body data
            signed: Whether to sign the request
            retries: Number of retries on failure
            
        Returns:
            JSON response as dictionary
        """
        async with self.rate_limiter:
            url = f"{self.base_url}{endpoint}"
            headers = {}
            
            if signed:
                headers = self._sign_request(method, endpoint, params or {})
            
            for attempt in range(retries):
                try:
                    if method.upper() == "GET":
                        response = await self.client.get(url, params=params, headers=headers)
                    elif method.upper() == "POST":
                        response = await self.client.post(url, json=data, headers=headers)
                    elif method.upper() == "DELETE":
                        response = await self.client.delete(url, params=params, headers=headers)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")
                    
                    response.raise_for_status()
                    return response.json()
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:  # Rate limited
                        wait_time = 2 ** attempt
                        self.logger.logger.warning(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    elif e.response.status_code >= 500:  # Server error, retry
                        if attempt < retries - 1:
                            await asyncio.sleep(1)
                            continue
                    raise
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    if attempt < retries - 1:
                        await asyncio.sleep(1)
                        continue
                    raise
            
            raise Exception(f"Request failed after {retries} attempts")
    
    # Abstract methods that each exchange must implement
    
    @abstractmethod
    async def get_account(self) -> Dict[str, Any]:
        """Get account information."""
        pass
    
    @abstractmethod
    async def get_balances(self) -> List[Balance]:
        """Get account balances."""
        pass
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for symbol."""
        pass
    
    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get order book for symbol."""
        pass
    
    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        """Place a new order."""
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing order."""
        pass
    
    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Get order status."""
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        pass
    
    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get current positions."""
        pass
    
    @abstractmethod
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV data."""
        pass
    
    async def subscribe_ticker(self, symbol: str, callback: Callable) -> None:
        """Subscribe to real-time ticker updates via WebSocket.
        
        Args:
            symbol: Trading symbol
            callback: Function to call with ticker updates
        """
        raise NotImplementedError("WebSocket not implemented for this exchange")


class ExchangeError(Exception):
    """Base exception for exchange errors."""
    pass


class AuthenticationError(ExchangeError):
    """Authentication failed."""
    pass


class InsufficientFundsError(ExchangeError):
    """Insufficient funds for operation."""
    pass


class InvalidSymbolError(ExchangeError):
    """Invalid trading symbol."""
    pass
