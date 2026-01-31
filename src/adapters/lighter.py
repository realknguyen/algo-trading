"""LIGHTER exchange adapter implementation.

This module provides a comprehensive adapter for the LIGHTER decentralized
trading platform (zkLighter), supporting both REST API and WebSocket connections
with auto-reconnect capabilities.

Features:
    - API key authentication with Ed25519 signing
    - Perpetual futures and spot trading support
    - Testnet/mainnet support
    - WebSocket auto-reconnect with exponential backoff
    - Rate limiting per exchange specifications
    - Funding rate tracking
    - Position and order management

References:
    - https://docs.lighter.xyz/trading/api
    - https://apidocs.lighter.xyz/
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set, Union, AsyncIterator
from urllib.parse import urlencode

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

# Import from parent adapters module
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from adapters.base_adapter import (
        BaseExchangeAdapter, Order, OrderType, OrderSide,
        OrderStatus, TimeInForce, Ticker, Position, Balance,
        Candle, OrderBook, AccountInfo, RetryConfig
    )
    from adapters.exceptions import (
        ExchangeError, ExchangeConnectionError, AuthenticationError,
        RateLimitError, OrderError, InsufficientFundsError,
        InvalidSymbolError, WebSocketError, DataValidationError
    )
except ImportError:
    # Fallback for when running from src/adapters directly
    from base_adapter import (
        BaseExchangeAdapter, Order, OrderType, OrderSide,
        OrderStatus, TimeInForce, Ticker, Position, Balance,
        Candle, OrderBook, AccountInfo, RetryConfig
    )
    from exceptions import (
        ExchangeError, ExchangeConnectionError, AuthenticationError,
        RateLimitError, OrderError, InsufficientFundsError,
        InvalidSymbolError, WebSocketError, DataValidationError
    )


logger = logging.getLogger(__name__)


# LIGHTER-specific constants
class LighterOrderType:
    """Order type constants for LIGHTER."""
    LIMIT = "ORDER_TYPE_LIMIT"
    MARKET = "ORDER_TYPE_MARKET"
    STOP_LOSS = "ORDER_TYPE_STOP_LOSS"
    STOP_LOSS_LIMIT = "ORDER_TYPE_STOP_LOSS_LIMIT"
    TAKE_PROFIT = "ORDER_TYPE_TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "ORDER_TYPE_TAKE_PROFIT_LIMIT"
    TWAP = "ORDER_TYPE_TWAP"


class LighterTimeInForce:
    """Time in force constants for LIGHTER."""
    IOC = "ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    GTT = "ORDER_TIME_IN_FORCE_GOOD_TILL_TIME"
    POST_ONLY = "ORDER_TIME_IN_FORCE_POST_ONLY"


class LighterTransactionType:
    """Transaction type constants for LIGHTER."""
    CREATE_ORDER = 1
    CANCEL_ORDER = 2
    MODIFY_ORDER = 3
    CANCEL_ALL_ORDERS = 4
    AUTH_TOKEN = 15


class LighterError(ExchangeError):
    """Raised when LIGHTER API returns an error."""
    def __init__(self, message: str, code: int = None, status: str = None):
        super().__init__(message)
        self.code = code
        self.status = status


class LighterRateLimitError(RateLimitError):
    """Raised when LIGHTER rate limit is exceeded."""
    pass


@dataclass
class FundingRate:
    """Funding rate dataclass.
    
    Attributes:
        symbol: Trading pair symbol
        market_id: Market identifier
        funding_rate: Current funding rate
        current_funding_rate: Predicted next funding rate
        funding_timestamp: When next funding occurs
        index_price: Current index price
        mark_price: Current mark price
        timestamp: Data timestamp
    """
    symbol: str
    market_id: int
    funding_rate: Decimal
    current_funding_rate: Optional[Decimal] = None
    funding_timestamp: Optional[int] = None
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    timestamp: Optional[int] = None


@dataclass
class LighterPosition:
    """LIGHTER position dataclass.
    
    Attributes:
        market_id: Market identifier
        symbol: Trading pair symbol
        position: Position size (string representation)
        sign: 1 for long, -1 for short
        avg_entry_price: Average entry price
        position_value: Current position value
        unrealized_pnl: Unrealized profit/loss
        realized_pnl: Realized profit/loss
        liquidation_price: Estimated liquidation price
        margin_mode: Margin mode (1=cross, 2=isolated)
        allocated_margin: Allocated margin amount
        initial_margin_fraction: Initial margin fraction
    """
    market_id: int
    symbol: str
    position: str
    sign: int
    avg_entry_price: str
    position_value: str
    unrealized_pnl: str
    realized_pnl: str
    liquidation_price: str
    margin_mode: int
    allocated_margin: str
    initial_margin_fraction: str


class LighterAdapter(BaseExchangeAdapter):
    """LIGHTER exchange adapter with Ed25519 signing and WebSocket support.
    
    Supports perpetual futures and spot trading on the LIGHTER zk-rollup.
    Uses Ed25519 signatures for API authentication. Requires an Ethereum
    wallet for account creation and API key management.
    
    Args:
        api_key: API key identifier (account index or L1 address)
        api_secret: API private key for signing (hex string)
        account_index: Account index for the API key
        api_key_index: API key index (3-254 for user keys)
        sandbox: Use testnet (default: True)
        rate_limit_per_second: Requests per second limit (default: 10.0)
        eth_private_key: Ethereum private key for L1 operations (optional)
    
    Example:
        >>> adapter = LighterAdapter(
        ...     api_key="12345",  # Account index
        ...     api_secret="0x...",  # API private key
        ...     account_index=12345,
        ...     api_key_index=3,
        ...     sandbox=True
        ... )
        >>> async with adapter:
        ...     # Get account info
        ...     account = await adapter.get_account()
        ...     
        ...     # Place a limit order
        ...     order = Order(
        ...         symbol="ETH-USD",
        ...         side=OrderSide.BUY,
        ...         order_type=OrderType.LIMIT,
        ...         quantity=Decimal("0.1"),
        ...         price=Decimal("3000")
        ...     )
        ...     placed = await adapter.place_order(order)
    """
    
    # Exchange identification
    exchange_name = "lighter"
    
    # API URLs
    MAINNET_API_URL = "https://mainnet.zklighter.elliot.ai"
    TESTNET_API_URL = "https://testnet.zklighter.elliot.ai"
    MAINNET_WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
    TESTNET_WS_URL = "wss://testnet.zklighter.elliot.ai/stream"
    
    # Explorer API for public data
    EXPLORER_API_URL = "https://explorer.elliot.ai/api"
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        account_index: int,
        api_key_index: int = 3,
        sandbox: bool = True,
        rate_limit_per_second: float = 10.0,
        eth_private_key: Optional[str] = None,
        **kwargs
    ):
        """Initialize the LIGHTER adapter.
        
        Args:
            api_key: Account identifier (account index or L1 address)
            api_secret: API private key for request signing (hex string)
            account_index: Numeric account index
            api_key_index: API key index (3-254 for user keys, 255 for all)
            sandbox: Use testnet environment
            rate_limit_per_second: API rate limit
            eth_private_key: Ethereum private key for L1 transactions
            **kwargs: Additional parameters
        """
        self.account_index = int(account_index)
        self.api_key_index = int(api_key_index)
        self.eth_private_key = eth_private_key
        
        # Track nonces per API key
        self._nonces: Dict[int, int] = {}
        self._nonce_lock = asyncio.Lock()
        
        # Market info cache
        self._markets: Dict[str, Dict[str, Any]] = {}
        self._market_id_to_symbol: Dict[int, str] = {}
        self._symbol_to_market_id: Dict[str, int] = {}
        
        # Auth token for WebSocket
        self._auth_token: Optional[str] = None
        self._auth_token_expiry: Optional[int] = None
        
        # WebSocket state
        self._ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._ws_reconnect_attempts: Dict[str, int] = {}
        self._ws_running: Dict[str, bool] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        self._ws_subscriptions: Dict[str, Set[str]] = {}
        
        # Select appropriate base URL
        base_url = self.TESTNET_API_URL if sandbox else self.MAINNET_API_URL
        ws_url = self.TESTNET_WS_URL if sandbox else self.MAINNET_WS_URL
        
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            auth_type="ed25519",
            ws_url=ws_url,
            **kwargs
        )
    
    # ==================== Connection Management ====================
    
    async def connect(self) -> bool:
        """Connect to LIGHTER and validate credentials.
        
        Establishes connection by:
        1. Loading market metadata
        2. Validating API credentials
        3. Synchronizing nonce
        
        Returns:
            True if connection successful
            
        Raises:
            ExchangeConnectionError: If connection fails
            AuthenticationError: If credentials are invalid
        """
        try:
            # Load market data
            await self._load_markets()
            
            # Validate credentials by fetching account info
            await self.get_account()
            
            # Get next nonce for this API key
            await self._sync_nonce()
            
            self._connected = True
            logger.info("LIGHTER adapter connected successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to LIGHTER: {e}")
            raise ExchangeConnectionError(
                f"Connection failed: {e}",
                exchange=self.exchange_name
            )
    
    async def disconnect(self) -> None:
        """Disconnect from LIGHTER and cleanup resources."""
        # Close all WebSocket connections
        for channel, ws in list(self._ws_connections.items()):
            try:
                await ws.close()
            except Exception:
                pass
        
        self._ws_connections.clear()
        self._ws_running.clear()
        
        # Cancel all WebSocket tasks
        for task in self._ws_tasks.values():
            task.cancel()
        
        self._ws_tasks.clear()
        
        # Close HTTP client
        await self._client.aclose()
        self._connected = False
        logger.info("LIGHTER adapter disconnected")
    
    async def _load_markets(self) -> None:
        """Load market metadata from explorer API."""
        try:
            response = await self._make_public_request("GET", "/markets")
            
            for market in response.get("markets", []):
                symbol = market.get("symbol")
                market_id = market.get("market_id")
                
                if symbol and market_id is not None:
                    self._markets[symbol] = market
                    self._market_id_to_symbol[market_id] = symbol
                    self._symbol_to_market_id[symbol] = market_id
                    
        except Exception as e:
            logger.warning(f"Failed to load markets: {e}")
    
    async def _sync_nonce(self) -> None:
        """Synchronize nonce from server."""
        try:
            nonce = await self._get_next_nonce()
            async with self._nonce_lock:
                self._nonces[self.api_key_index] = nonce
        except Exception as e:
            logger.warning(f"Failed to sync nonce: {e}")
            # Start with 0 if we can't get nonce
            async with self._nonce_lock:
                self._nonces[self.api_key_index] = 0
    
    async def _get_next_nonce(self) -> int:
        """Get next nonce from server for the API key."""
        params = {
            "account_index": self.account_index,
            "api_key_index": self.api_key_index
        }
        
        response = await self._make_request(
            "GET", 
            "/api/transaction/next_nonce",
            params=params
        )
        
        return response.get("nonce", 0)
    
    async def _get_nonce(self) -> int:
        """Get and increment local nonce."""
        async with self._nonce_lock:
            current = self._nonces.get(self.api_key_index, 0)
            self._nonces[self.api_key_index] = current + 1
            return current
    
    # ==================== Authentication ====================
    
    def _sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """Sign request with Ed25519.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Request body
            
        Returns:
            Headers dictionary with authentication info
        """
        import time
        timestamp = int(time.time() * 1000)
        
        # Create payload string
        payload_parts = [method.upper(), endpoint, str(timestamp)]
        
        if params:
            payload_parts.append(json.dumps(params, sort_keys=True, separators=(',', ':')))
        if data:
            payload_parts.append(json.dumps(data, sort_keys=True, separators=(',', ':')))
        
        payload = "|".join(payload_parts)
        
        # Sign with Ed25519
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            
            # Convert hex private key to bytes
            private_key_hex = self._api_secret.replace("0x", "")
            private_key_bytes = bytes.fromhex(private_key_hex)
            
            # Create private key object
            private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
            
            # Sign payload
            signature = private_key.sign(payload.encode())
            signature_hex = signature.hex()
            
        except Exception as e:
            logger.error(f"Failed to sign request: {e}")
            raise AuthenticationError(
                f"Signing failed: {e}",
                exchange=self.exchange_name
            )
        
        return {
            "X-API-Key": str(self.api_key_index),
            "X-Account-Index": str(self.account_index),
            "X-Timestamp": str(timestamp),
            "X-Signature": signature_hex,
            "Content-Type": "application/json"
        }
    
    async def _generate_auth_token(self, expiry_seconds: int = 3600) -> str:
        """Generate authentication token for WebSocket.
        
        Args:
            expiry_seconds: Token validity period
            
        Returns:
            Auth token string
        """
        # This would create and sign an auth token transaction
        # For now, return a placeholder
        # In production, this should create a proper auth token transaction
        return "auth_token_placeholder"
    
    # ==================== HTTP Request Helpers ====================
    
    async def _make_public_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make public request to explorer API.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response
        """
        url = f"{self.EXPLORER_API_URL}{endpoint}"
        
        async with self._rate_limiter:
            try:
                if method.upper() == "GET":
                    response = await self._client.get(url, params=params)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                response.raise_for_status()
                return response.json()
                
            except httpx.HTTPStatusError as e:
                self._handle_http_error(e.response.status_code, e.response)
            except Exception as e:
                raise ExchangeConnectionError(
                    f"Request failed: {e}",
                    exchange=self.exchange_name
                )
    
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
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            data: Request body
            signed: Whether to sign the request
            retries: Number of retry attempts
            
        Returns:
            JSON response
        """
        url = f"{self._base_url}{endpoint}"
        headers = {}
        
        if signed:
            headers = self._sign_request(method, endpoint, params, data)
        
        async with self._rate_limiter:
            for attempt in range(retries):
                try:
                    if method.upper() == "GET":
                        response = await self._client.get(
                            url, params=params, headers=headers
                        )
                    elif method.upper() == "POST":
                        response = await self._client.post(
                            url, json=data, headers=headers
                        )
                    elif method.upper() == "DELETE":
                        response = await self._client.delete(
                            url, params=params, headers=headers
                        )
                    else:
                        raise ValueError(f"Unsupported method: {method}")
                    
                    # Handle rate limiting
                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 1))
                        logger.warning(f"Rate limited. Retry after {retry_after}s")
                        if attempt < retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise LighterRateLimitError(
                            "Rate limit exceeded",
                            exchange=self.exchange_name,
                            retry_after=retry_after
                        )
                    
                    response.raise_for_status()
                    
                    result = response.json()
                    
                    # Check for API-level errors
                    if isinstance(result, dict) and result.get("error"):
                        raise LighterError(
                            result.get("message", "Unknown error"),
                            code=result.get("code")
                        )
                    
                    return result
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500 and attempt < retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Server error, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    self._handle_http_error(e.response.status_code, e.response)
                    
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    if attempt < retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"Connection error, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    raise ExchangeConnectionError(
                        f"Connection failed: {e}",
                        exchange=self.exchange_name
                    )
            
            raise ExchangeError("Request failed after max retries")
    
    def _handle_http_error(self, status_code: int, response: httpx.Response) -> None:
        """Handle HTTP errors and raise appropriate exceptions."""
        try:
            error_data = response.json()
            message = error_data.get("message", "Unknown error")
            code = error_data.get("code")
        except:
            message = response.text or f"HTTP {status_code}"
            code = None
        
        if status_code == 401:
            raise AuthenticationError(
                f"Authentication failed: {message}",
                exchange=self.exchange_name
            )
        elif status_code == 429:
            raise LighterRateLimitError(
                f"Rate limit exceeded: {message}",
                exchange=self.exchange_name
            )
        elif status_code == 404:
            raise InvalidSymbolError(
                f"Resource not found: {message}",
                exchange=self.exchange_name
            )
        else:
            raise LighterError(
                f"HTTP {status_code}: {message}",
                code=code
            )
    
    # ==================== REST API Methods ====================
    
    async def get_account(self) -> AccountInfo:
        """Get account information.
        
        Returns:
            AccountInfo object with account details
            
        Raises:
            AuthenticationError: If authentication fails
        """
        params = {
            "account_index": self.account_index
        }
        
        response = await self._make_request(
            "GET",
            "/api/account",
            params=params,
            signed=True
        )
        
        account_data = response.get("account", {})
        
        return AccountInfo(
            account_id=str(account_data.get("index", self.account_index)),
            account_type=account_data.get("type", "standard"),
            permissions=account_data.get("permissions", []),
            created_at=account_data.get("created_at")
        )
    
    async def get_balances(self) -> List[Balance]:
        """Get all account balances.
        
        Returns:
            List of Balance objects
        """
        params = {
            "account_index": self.account_index
        }
        
        response = await self._make_request(
            "GET",
            "/api/account/assets",
            params=params,
            signed=True
        )
        
        balances = []
        assets = response.get("assets", {})
        
        for asset_id, asset_data in assets.items():
            symbol = asset_data.get("symbol", f"ASSET_{asset_id}")
            balance_str = asset_data.get("balance", "0")
            locked_str = asset_data.get("locked_balance", "0")
            
            free = Decimal(balance_str)
            locked = Decimal(locked_str)
            
            if free > 0 or locked > 0:
                balances.append(Balance(
                    asset=symbol,
                    free=free,
                    locked=locked,
                    total=free + locked
                ))
        
        return balances
    
    async def get_positions(self) -> List[Position]:
        """Get current positions.
        
        Returns:
            List of Position objects
        """
        params = {
            "account_index": self.account_index
        }
        
        response = await self._make_request(
            "GET",
            "/api/account/positions",
            params=params,
            signed=True
        )
        
        positions = []
        positions_data = response.get("positions", {})
        
        for market_id, pos_data in positions_data.items():
            symbol = self._market_id_to_symbol.get(
                int(market_id), f"MARKET_{market_id}"
            )
            
            sign = pos_data.get("sign", 1)
            position_str = pos_data.get("position", "0")
            quantity = Decimal(position_str) * Decimal(sign)
            
            positions.append(Position(
                symbol=symbol,
                quantity=quantity,
                avg_entry_price=Decimal(pos_data.get("avg_entry_price", "0")),
                current_price=Decimal(pos_data.get("mark_price", "0")),
                unrealized_pnl=Decimal(pos_data.get("unrealized_pnl", "0")),
                realized_pnl=Decimal(pos_data.get("realized_pnl", "0")),
                leverage=Decimal("1"),  # Will be calculated from margin
                margin_mode="cross" if pos_data.get("margin_mode") == 1 else "isolated"
            ))
        
        return positions
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., "ETH-USD")
            
        Returns:
            Ticker object with current prices
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            # Fallback to public API
            response = await self._make_public_request(
                "GET",
                f"/ticker/{symbol}"
            )
        else:
            # Use WebSocket API for real-time data
            params = {"market_index": market_id}
            response = await self._make_request(
                "GET",
                "/api/market/stats",
                params=params
            )
        
        stats = response.get("market_stats", response)
        
        return Ticker(
            symbol=symbol,
            bid=Decimal(stats.get("best_bid", "0")),
            ask=Decimal(stats.get("best_ask", "0")),
            last=Decimal(stats.get("last_trade_price", "0")),
            volume=Decimal(str(stats.get("daily_base_token_volume", 0))),
            timestamp=time.time(),
            high_24h=Decimal(str(stats.get("daily_price_high", 0))),
            low_24h=Decimal(str(stats.get("daily_price_low", 0)))
        )
    
    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """Get order book for symbol.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of levels (not directly used by LIGHTER API)
            
        Returns:
            OrderBook object
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        params = {"market_index": market_id}
        
        response = await self._make_request(
            "GET",
            "/api/order/book_details",
            params=params
        )
        
        order_book = response.get("order_book", {})
        
        # Parse bids and asks
        bids = [
            [Decimal(b["price"]), Decimal(b["size"])]
            for b in order_book.get("bids", [])
        ]
        asks = [
            [Decimal(a["price"]), Decimal(a["size"])]
            for a in order_book.get("asks", [])
        ]
        
        return OrderBook(
            symbol=symbol,
            bids=bids[:limit],
            asks=asks[:limit],
            timestamp=time.time(),
            sequence=order_book.get("nonce")
        )
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[Candle]:
        """Get historical OHLCV candles.
        
        Args:
            symbol: Trading pair symbol
            interval: Candle interval (e.g., "1m", "5m", "1h", "1d")
            start_time: Start timestamp (Unix seconds)
            end_time: End timestamp (Unix seconds)
            limit: Maximum candles to return
            
        Returns:
            List of Candle objects
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        # Convert interval to LIGHTER format
        interval_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400
        }
        resolution = interval_map.get(interval, 3600)
        
        params: Dict[str, Any] = {
            "market_index": market_id,
            "resolution": resolution,
            "limit": limit
        }
        
        if start_time:
            params["from"] = start_time
        if end_time:
            params["to"] = end_time
        
        response = await self._make_request(
            "GET",
            "/api/market/candles",
            params=params
        )
        
        candles = []
        for c in response.get("candles", []):
            candles.append(Candle(
                timestamp=c.get("time", 0),
                open=Decimal(str(c.get("open", 0))),
                high=Decimal(str(c.get("high", 0))),
                low=Decimal(str(c.get("low", 0))),
                close=Decimal(str(c.get("close", 0))),
                volume=Decimal(str(c.get("volume", 0)))
            ))
        
        return candles
    
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Get funding rate for symbol.
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            FundingRate object
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        params = {"market_index": market_id}
        
        response = await self._make_request(
            "GET",
            "/api/market/stats",
            params=params
        )
        
        stats = response.get("market_stats", {})
        
        return FundingRate(
            symbol=symbol,
            market_id=market_id,
            funding_rate=Decimal(stats.get("funding_rate", "0")),
            current_funding_rate=Decimal(stats.get("current_funding_rate", "0")),
            funding_timestamp=stats.get("funding_timestamp"),
            index_price=Decimal(stats.get("index_price", "0")),
            mark_price=Decimal(stats.get("mark_price", "0")),
            timestamp=int(time.time() * 1000)
        )
    
    # ==================== Trading Methods ====================
    
    def _convert_order_type(self, order_type: OrderType) -> str:
        """Convert internal OrderType to LIGHTER format."""
        mapping = {
            OrderType.MARKET: LighterOrderType.MARKET,
            OrderType.LIMIT: LighterOrderType.LIMIT,
            OrderType.STOP_LOSS: LighterOrderType.STOP_LOSS,
            OrderType.STOP_LOSS_LIMIT: LighterOrderType.STOP_LOSS_LIMIT,
            OrderType.TAKE_PROFIT: LighterOrderType.TAKE_PROFIT,
            OrderType.TAKE_PROFIT_LIMIT: LighterOrderType.TAKE_PROFIT_LIMIT,
        }
        return mapping.get(order_type, LighterOrderType.MARKET)
    
    def _convert_time_in_force(self, tif: TimeInForce) -> str:
        """Convert internal TimeInForce to LIGHTER format."""
        mapping = {
            TimeInForce.IOC: LighterTimeInForce.IOC,
            TimeInForce.GTC: LighterTimeInForce.GTT,
        }
        return mapping.get(tif, LighterTimeInForce.GTT)
    
    async def place_order(self, order: Order) -> Order:
        """Place a new order.
        
        Args:
            order: Order object with trade details
            
        Returns:
            Updated Order with exchange-generated order_id
        """
        market_id = self._symbol_to_market_id.get(order.symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {order.symbol}",
                exchange=self.exchange_name,
                symbol=order.symbol
            )
        
        # Get nonce
        nonce = await self._get_nonce()
        
        # Generate client order index if not provided
        client_order_index = int(order.client_order_id) if order.client_order_id else int(time.time())
        
        # Convert amounts to integers (base_amount, price)
        base_amount = int(order.quantity * Decimal("1e8"))  # Assuming 8 decimals
        
        price = 0
        if order.price:
            price = int(order.price * Decimal("1e8"))
        
        # Build order transaction
        order_data = {
            "account_index": self.account_index,
            "api_key_index": self.api_key_index,
            "market_index": market_id,
            "order_type": self._convert_order_type(order.order_type),
            "time_in_force": self._convert_time_in_force(order.time_in_force),
            "side": "ASK" if order.side == OrderSide.SELL else "BID",
            "base_amount": str(base_amount),
            "price": str(price),
            "client_order_index": client_order_index,
            "nonce": nonce,
            "is_ask": order.side == OrderSide.SELL,
            "reduce_only": False
        }
        
        # Sign and send transaction
        tx_data = await self._sign_transaction(
            LighterTransactionType.CREATE_ORDER,
            order_data
        )
        
        response = await self._make_request(
            "POST",
            "/api/transaction/send_tx",
            data={
                "tx_type": LighterTransactionType.CREATE_ORDER,
                "tx_info": tx_data
            },
            signed=True
        )
        
        # Update order with response data
        order.order_id = str(response.get("order_index", client_order_index))
        order.status = OrderStatus.OPEN
        order.client_order_id = str(client_order_index)
        
        return order
    
    async def _sign_transaction(
        self,
        tx_type: int,
        tx_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Sign a transaction with Ed25519.
        
        Args:
            tx_type: Transaction type constant
            tx_info: Transaction data
            
        Returns:
            Signed transaction data
        """
        # This is a simplified signing implementation
        # In production, this would use the proper Ed25519 signing
        # with the lighter-go binary or equivalent
        
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            
            # Convert hex private key
            private_key_hex = self._api_secret.replace("0x", "")
            private_key_bytes = bytes.fromhex(private_key_hex)
            
            # Create private key
            private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
            
            # Create payload
            payload = json.dumps({
                "type": tx_type,
                "info": tx_info
            }, sort_keys=True, separators=(',', ':'))
            
            # Sign
            signature = private_key.sign(payload.encode())
            
            tx_info["signature"] = signature.hex()
            return tx_info
            
        except Exception as e:
            raise AuthenticationError(
                f"Transaction signing failed: {e}",
                exchange=self.exchange_name
            )
    
    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancel an existing order.
        
        Args:
            order_id: Order ID to cancel (client_order_index)
            symbol: Trading symbol (required for some exchanges)
            
        Returns:
            True if cancellation successful
        """
        market_id = None
        if symbol:
            market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Symbol required for cancel: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        nonce = await self._get_nonce()
        
        cancel_data = {
            "account_index": self.account_index,
            "api_key_index": self.api_key_index,
            "market_index": market_id,
            "order_index": int(order_id),
            "nonce": nonce
        }
        
        tx_data = await self._sign_transaction(
            LighterTransactionType.CANCEL_ORDER,
            cancel_data
        )
        
        await self._make_request(
            "POST",
            "/api/transaction/send_tx",
            data={
                "tx_type": LighterTransactionType.CANCEL_ORDER,
                "tx_info": tx_data
            },
            signed=True
        )
        
        return True
    
    async def get_order_status(self, order_id: str, symbol: Optional[str] = None) -> Order:
        """Get order status by ID.
        
        Args:
            order_id: Exchange order ID
            symbol: Trading symbol (optional)
            
        Returns:
            Order with current status
        """
        params = {
            "account_index": self.account_index,
            "order_index": int(order_id)
        }
        
        if symbol:
            market_id = self._symbol_to_market_id.get(symbol)
            if market_id:
                params["market_index"] = market_id
        
        response = await self._make_request(
            "GET",
            "/api/order",
            params=params,
            signed=True
        )
        
        order_data = response.get("order", {})
        
        # Map LIGHTER status to internal status
        status_map = {
            "open": OrderStatus.OPEN,
            "filled": OrderStatus.FILLED,
            "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "pending": OrderStatus.PENDING
        }
        
        symbol = self._market_id_to_symbol.get(
            order_data.get("market_index", 0),
            symbol or "UNKNOWN"
        )
        
        return Order(
            symbol=symbol,
            side=OrderSide.SELL if order_data.get("is_ask") else OrderSide.BUY,
            order_type=OrderType.LIMIT,  # Simplified
            quantity=Decimal(order_data.get("initial_base_amount", "0")),
            price=Decimal(order_data.get("price", "0")),
            order_id=str(order_data.get("order_index", order_id)),
            client_order_id=str(order_data.get("client_order_index", "")),
            status=status_map.get(order_data.get("status", ""), OrderStatus.PENDING),
            filled_quantity=Decimal(order_data.get("filled_base_amount", "0"))
        )
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.
        
        Args:
            symbol: Filter by symbol (optional)
            
        Returns:
            List of open orders
        """
        params = {
            "account_index": self.account_index
        }
        
        if symbol:
            market_id = self._symbol_to_market_id.get(symbol)
            if market_id:
                params["market_index"] = market_id
        
        response = await self._make_request(
            "GET",
            "/api/account/orders",
            params=params,
            signed=True
        )
        
        orders = []
        orders_data = response.get("orders", {})
        
        for market_id, market_orders in orders_data.items():
            sym = self._market_id_to_symbol.get(int(market_id), f"MARKET_{market_id}")
            
            for order_data in market_orders:
                if order_data.get("status") == "open":
                    orders.append(Order(
                        symbol=sym,
                        side=OrderSide.SELL if order_data.get("is_ask") else OrderSide.BUY,
                        order_type=OrderType.LIMIT,
                        quantity=Decimal(order_data.get("initial_base_amount", "0")),
                        price=Decimal(order_data.get("price", "0")),
                        order_id=str(order_data.get("order_index", "")),
                        client_order_id=str(order_data.get("client_order_index", "")),
                        status=OrderStatus.OPEN,
                        filled_quantity=Decimal(order_data.get("filled_base_amount", "0"))
                    ))
        
        return orders
    
    # ==================== WebSocket Methods ====================
    
    async def subscribe_to_trades(
        self,
        symbol: str,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to real-time trade updates.
        
        Args:
            symbol: Trading pair symbol
            callback: Function to call with trade updates
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        channel = f"trade/{market_id}"
        await self._ws_subscribe(channel, callback)
    
    async def subscribe_to_orderbook(
        self,
        symbol: str,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to order book updates.
        
        Args:
            symbol: Trading pair symbol
            callback: Function to call with orderbook updates
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        channel = f"order_book/{market_id}"
        await self._ws_subscribe(channel, callback)
    
    async def subscribe_to_ticker(
        self,
        symbol: str,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to ticker/price updates.
        
        Args:
            symbol: Trading pair symbol
            callback: Function to call with ticker updates
        """
        market_id = self._symbol_to_market_id.get(symbol)
        
        if market_id is None:
            raise InvalidSymbolError(
                f"Unknown symbol: {symbol}",
                exchange=self.exchange_name,
                symbol=symbol
            )
        
        channel = f"market_stats/{market_id}"
        await self._ws_subscribe(channel, callback)
    
    async def subscribe_to_user(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to private account updates.
        
        Requires authentication. Provides updates on:
        - Orders
        - Positions
        - Trades
        - Balances
        
        Args:
            callback: Function to call with account updates
        """
        # Generate auth token for WebSocket
        auth_token = await self._generate_auth_token()
        
        # Subscribe to account_all channel
        channel = f"account_all/{self.account_index}"
        await self._ws_subscribe(channel, callback, auth=auth_token)
    
    async def _ws_subscribe(
        self,
        channel: str,
        callback: Callable[[Dict[str, Any]], None],
        auth: Optional[str] = None
    ) -> None:
        """Subscribe to WebSocket channel.
        
        Args:
            channel: Channel name
            callback: Callback function
            auth: Optional auth token
        """
        # Store callback
        if channel not in self._ws_callbacks:
            self._ws_callbacks[channel] = []
        self._ws_callbacks[channel].append(callback)
        
        # Start WebSocket connection if not running
        if not self._ws_running.get(channel):
            task = asyncio.create_task(
                self._ws_connection_loop(channel, auth)
            )
            self._ws_tasks[channel] = task
            self._ws_running[channel] = True
        else:
            # Send subscribe message on existing connection
            ws = self._ws_connections.get(channel)
            if ws:
                msg = {
                    "type": "subscribe",
                    "channel": channel
                }
                if auth:
                    msg["auth"] = auth
                await ws.send(json.dumps(msg))
    
    async def _ws_connection_loop(
        self,
        channel: str,
        auth: Optional[str] = None
    ) -> None:
        """Maintain WebSocket connection with auto-reconnect.
        
        Args:
            channel: Channel to subscribe to
            auth: Optional auth token
        """
        while self._ws_running.get(channel, False):
            try:
                # Determine if we need readonly mode
                readonly = not channel.startswith("account") and not auth
                ws_url = self._ws_url
                
                if readonly:
                    ws_url += "?readonly=true"
                
                async with websockets.connect(ws_url) as ws:
                    self._ws_connections[channel] = ws
                    self._ws_reconnect_attempts[channel] = 0
                    
                    # Send subscribe message
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": channel
                    }
                    if auth:
                        subscribe_msg["auth"] = auth
                    
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"Subscribed to WebSocket channel: {channel}")
                    
                    # Handle incoming messages
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            await self._handle_ws_message(channel, data)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid WebSocket message: {message}")
                            
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            
            # Reconnect with exponential backoff
            if self._ws_running.get(channel, False):
                attempts = self._ws_reconnect_attempts.get(channel, 0)
                delay = min(2 ** attempts, 60)  # Max 60 seconds
                self._ws_reconnect_attempts[channel] = attempts + 1
                
                logger.info(f"Reconnecting to {channel} in {delay}s...")
                await asyncio.sleep(delay)
    
    async def _handle_ws_message(
        self,
        channel: str,
        data: Dict[str, Any]
    ) -> None:
        """Handle WebSocket message.
        
        Args:
            channel: Channel name
            data: Message data
        """
        # Call registered callbacks
        callbacks = self._ws_callbacks.get(channel, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"WebSocket callback error: {e}")
    
    async def unsubscribe_market_data(
        self,
        symbols: Optional[List[str]] = None,
        channels: Optional[List[str]] = None
    ) -> None:
        """Unsubscribe from market data.
        
        Args:
            symbols: Symbols to unsubscribe (None = all)
            channels: Specific channels to unsubscribe (None = all)
        """
        if channels:
            for channel in channels:
                await self._ws_unsubscribe(channel)
        elif symbols:
            for symbol in symbols:
                market_id = self._symbol_to_market_id.get(symbol)
                if market_id:
                    for prefix in ["trade", "order_book", "market_stats"]:
                        await self._ws_unsubscribe(f"{prefix}/{market_id}")
        else:
            # Unsubscribe from all
            for channel in list(self._ws_connections.keys()):
                await self._ws_unsubscribe(channel)
    
    async def _ws_unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a specific channel.
        
        Args:
            channel: Channel to unsubscribe
        """
        ws = self._ws_connections.get(channel)
        if ws:
            try:
                await ws.send(json.dumps({
                    "type": "unsubscribe",
                    "channel": channel
                }))
            except Exception:
                pass
        
        # Stop the connection loop
        self._ws_running[channel] = False
        
        # Cancel the task
        task = self._ws_tasks.get(channel)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Clean up
        self._ws_callbacks.pop(channel, None)
        self._ws_connections.pop(channel, None)
        self._ws_tasks.pop(channel, None)
        self._ws_running.pop(channel, None)
    
    # ==================== Additional Methods ====================
    
    async def get_markets(self) -> List[Dict[str, Any]]:
        """Get all available markets.
        
        Returns:
            List of market information dictionaries
        """
        if not self._markets:
            await self._load_markets()
        
        return list(self._markets.values())
    
    async def get_server_time(self) -> int:
        """Get server time in milliseconds.
        
        Returns:
            Unix timestamp in milliseconds
        """
        response = await self._make_request("GET", "/")
        return response.get("timestamp", int(time.time() * 1000))


# Import dataclass at the end to avoid circular issues
from dataclasses import dataclass
