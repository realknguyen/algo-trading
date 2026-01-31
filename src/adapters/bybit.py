"""Bybit exchange adapter implementation for API v5.

This module provides a comprehensive adapter for the Bybit cryptocurrency exchange,
supporting both REST API v5 and WebSocket connections with auto-reconnect capabilities.

Features:
    - HMAC SHA256 request signing (Bybit v5 format)
    - Unified Trading Account (UTA) support
    - Category parameter support (spot, linear, inverse, option)
    - Testnet support toggle
    - Rate limit handling
    - WebSocket auto-reconnect with exponential backoff
    - Support for all market types: Spot, USDT Perpetual, Inverse Perpetual, 
      Inverse Futures, Options
    - TP/SL mode support (Full, Partial)
    - TimeInForce: GTC, IOC, FOK, PostOnly
    - Conditional orders support

Reference: https://bybit-exchange.github.io/docs/v5/intro
"""

import asyncio
import hashlib
import hmac
import json
import random
import time
from decimal import Decimal
from typing import Optional, Dict, Any, List, Callable, Set, Union
from urllib.parse import urlencode
from enum import Enum
from dataclasses import dataclass, field

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

# Import from parent adapters module
try:
    from src.adapters.base_adapter import (
        BaseExchangeAdapter, Order, OrderType, OrderSide, 
        OrderStatus, TimeInForce, Ticker, Position, Balance,
        Candle, OrderBook, AccountInfo, RetryConfig
    )
    from src.adapters.exceptions import (
        ExchangeError, AuthenticationError, RateLimitError,
        OrderError, InsufficientFundsError, InvalidSymbolError,
        ExchangeConnectionError, WebSocketError
    )
except ImportError:
    from base_adapter import (
        BaseExchangeAdapter, Order, OrderType, OrderSide, 
        OrderStatus, TimeInForce, Ticker, Position, Balance,
        Candle, OrderBook, AccountInfo, RetryConfig
    )
    from exceptions import (
        ExchangeError, AuthenticationError, RateLimitError,
        OrderError, InsufficientFundsError, InvalidSymbolError,
        ExchangeConnectionError, WebSocketError
    )


class BybitCategory(str, Enum):
    """Bybit product category enumeration."""
    SPOT = "spot"
    LINEAR = "linear"      # USDT/USDC Perpetuals
    INVERSE = "inverse"    # Coin-margined Perpetuals & Futures
    OPTION = "option"      # Options


class BybitOrderStatus(str, Enum):
    """Bybit order status enumeration."""
    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    TRIGGERED = "Triggered"
    DEACTIVATED = "Deactivated"
    ACTIVE = "Active"  # For conditional orders


class BybitTPSLMode(str, Enum):
    """Bybit TP/SL mode enumeration."""
    FULL = "Full"
    PARTIAL = "Partial"


class BybitTriggerBy(str, Enum):
    """Bybit trigger price type enumeration."""
    LAST_PRICE = "LastPrice"
    INDEX_PRICE = "IndexPrice"
    MARK_PRICE = "MarkPrice"


class BybitTimeInForce(str, Enum):
    """Bybit TimeInForce enumeration."""
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "PostOnly"


class BybitOrderType(str, Enum):
    """Bybit order type enumeration."""
    MARKET = "Market"
    LIMIT = "Limit"
    UNKNOWN = "Unknown"


@dataclass
class BybitAPIError(Exception):
    """Raised when Bybit API returns an error."""
    message: str
    code: int = 0
    
    def __str__(self) -> str:
        return f"Bybit API Error [{self.code}]: {self.message}"


class BybitAdapter(BaseExchangeAdapter):
    """Bybit exchange adapter with full REST and WebSocket support for API v5.
    
    Supports all Bybit product types through the category parameter:
    - Spot trading
    - USDT Perpetual (linear)
    - USDC Perpetual (linear)
    - Inverse Perpetual (coin-margined)
    - Inverse Futures (coin-margined)
    - Options
    
    Args:
        api_key: Bybit API key
        api_secret: Bybit API secret
        sandbox: Use testnet (default: True)
        rate_limit_per_second: Requests per second limit (default: 10.0)
        recv_window: Request validity window in milliseconds (default: 5000)
        
    Example:
        >>> adapter = BybitAdapter(
        ...     api_key="your_key",
        ...     api_secret="your_secret",
        ...     sandbox=True
        ... )
        >>> async with adapter:
        ...     # Get wallet balance
        ...     balances = await adapter.get_balances()
        ...     
        ...     # Place order on linear (USDT perp)
        ...     order = await adapter.place_order(
        ...         symbol="BTCUSDT",
        ...         side="Buy",
        ...         order_type="Limit",
        ...         qty="0.01",
        ...         price="25000",
        ...         category="linear"
        ...     )
    """
    
    # Exchange identification
    exchange_name = "bybit"
    
    # REST API Endpoints
    MAINNET_URL = "https://api.bybit.com"
    TESTNET_URL = "https://api-testnet.bybit.com"
    
    # WebSocket Endpoints
    WS_MAINNET_PUBLIC = "wss://stream.bybit.com/v5/public"
    WS_MAINNET_PRIVATE = "wss://stream.bybit.com/v5/private"
    WS_TESTNET_PUBLIC = "wss://stream-testnet.bybit.com/v5/public"
    WS_TESTNET_PRIVATE = "wss://stream-testnet.bybit.com/v5/private"
    
    # Category-specific WebSocket paths
    WS_SPOT_PATH = "spot"
    WS_LINEAR_PATH = "linear"
    WS_INVERSE_PATH = "inverse"
    WS_OPTION_PATH = "option"
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        rate_limit_per_second: float = 10.0,
        recv_window: int = 5000,
        **kwargs
    ):
        """Initialize Bybit adapter."""
        self.recv_window = recv_window
        self._ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._ws_reconnect_attempts: Dict[str, int] = {}
        self._ws_running: Dict[str, bool] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        self._ws_subscriptions: Dict[str, Set[str]] = {}
        
        # Select appropriate base URL
        base_url = self.TESTNET_URL if sandbox else self.MAINNET_URL
        
        # WebSocket URLs
        self._ws_public_url = self.WS_TESTNET_PUBLIC if sandbox else self.WS_MAINNET_PUBLIC
        self._ws_private_url = self.WS_TESTNET_PRIVATE if sandbox else self.WS_MAINNET_PRIVATE
        
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            **kwargs
        )
        
        # Override headers for Bybit
        self._client.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
    
    # ==================== Authentication ====================
    
    def _generate_signature(self, timestamp: str, api_key: str, recv_window: str, params: str) -> str:
        """Generate HMAC SHA256 signature for Bybit API v5.
        
        Args:
            timestamp: Current timestamp in milliseconds
            api_key: API key
            recv_window: Receive window in milliseconds
            params: Query string or JSON body
            
        Returns:
            Hex-encoded signature
        """
        # Bybit v5 signature format: timestamp + api_key + recv_window + params
        param_str = timestamp + api_key + recv_window + params
        signature = hmac.new(
            bytes(self._api_secret, "utf-8"),
            bytes(param_str, "utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_auth_headers(self, params: Optional[Union[Dict, str]] = None, is_json: bool = False) -> Dict[str, str]:
        """Generate authentication headers for Bybit API.
        
        Args:
            params: Query parameters or JSON body
            is_json: Whether params is a JSON string
            
        Returns:
            Dictionary of authentication headers
        """
        timestamp = str(int(time.time() * 1000))
        recv_window = str(self.recv_window)
        
        # Convert params to string
        if params is None:
            param_str = ""
        elif is_json:
            param_str = params if isinstance(params, str) else json.dumps(params, separators=(',', ':'))
        else:
            # For GET requests, use urlencoded params
            param_str = urlencode(sorted(params.items())) if params else ""
        
        signature = self._generate_signature(timestamp, self._api_key, recv_window, param_str)
        
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window
        }
    
    # ==================== REST API Helpers ====================
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        retries: int = 3
    ) -> Dict[str, Any]:
        """Make HTTP request to Bybit API with rate limiting and retry logic.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data
            signed: Whether to sign the request
            retries: Number of retry attempts
            
        Returns:
            JSON response dictionary
            
        Raises:
            BybitAPIError: If API returns error
            RateLimitError: If rate limit exceeded
            AuthenticationError: If authentication fails
        """
        params = params or {}
        data = data or {}
        
        url = f"{self._base_url}{endpoint}"
        headers = {}
        
        if signed:
            if method.upper() == "GET":
                auth_headers = self._get_auth_headers(params, is_json=False)
            else:
                # For POST/PUT, sign the JSON body
                json_body = json.dumps(data, separators=(',', ':')) if data else ""
                auth_headers = self._get_auth_headers(json_body, is_json=True)
            headers.update(auth_headers)
        
        for attempt in range(retries):
            try:
                async with self._rate_limiter:
                    if method.upper() == "GET":
                        response = await self._client.get(url, params=params, headers=headers)
                    elif method.upper() == "POST":
                        response = await self._client.post(url, json=data, headers=headers)
                    elif method.upper() == "PUT":
                        response = await self._client.put(url, json=data, headers=headers)
                    elif method.upper() == "DELETE":
                        response = await self._client.delete(url, params=params, headers=headers)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")
                    
                    # Parse response
                    result = response.json()
                    
                    # Check for Bybit API errors
                    if result.get("retCode") != 0:
                        error_msg = result.get("retMsg", "Unknown error")
                        error_code = result.get("retCode", -1)
                        
                        # Handle specific error codes
                        if error_code == 10001:  # Params error
                            raise OrderError(f"Invalid parameters: {error_msg}")
                        elif error_code in [10002, 10003, 10004]:  # Invalid request, IP banned, wrong sign
                            raise AuthenticationError(f"Authentication failed: {error_msg}")
                        elif error_code == 10006:  # Rate limit
                            raise RateLimitError(f"Rate limit exceeded: {error_msg}")
                        elif error_code == 110001:  # Order not found
                            raise OrderError(f"Order not found: {error_msg}")
                        elif error_code == 110003:  # Insufficient balance
                            raise InsufficientFundsError(f"Insufficient funds: {error_msg}")
                        elif error_code == 110012:  # Invalid symbol
                            raise InvalidSymbolError(f"Invalid symbol: {error_msg}")
                        else:
                            raise BybitAPIError(error_msg, error_code)
                    
                    return result
                    
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    retry_after = float(e.response.headers.get("Retry-After", 1))
                    if attempt < retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise RateLimitError(f"Rate limit exceeded", retry_after=retry_after)
                elif e.response.status_code in [401, 403]:
                    raise AuthenticationError(f"Authentication failed: {e.response.text}")
                elif e.response.status_code >= 500 and attempt < retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                raise ExchangeConnectionError(f"HTTP error {e.response.status_code}: {e.response.text}")
                
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < retries - 1:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                raise ExchangeConnectionError(f"Connection failed: {e}")
        
        raise ExchangeError("Request failed after maximum retries")
    
    # ==================== Connection Management ====================
    
    async def connect(self) -> bool:
        """Connect to Bybit and validate credentials.
        
        Returns:
            True if connection successful
            
        Raises:
            AuthenticationError: If credentials are invalid
            ExchangeConnectionError: If connection fails
        """
        try:
            # Test connection by getting server time
            server_time = await self.get_server_time()
            
            # Sync clock
            local_time = int(time.time() * 1000)
            skew = server_time - local_time
            if abs(skew) > 1000:  # More than 1 second difference
                # Update rate limiter's clock if needed
                pass
            
            # Test authentication with a simple API call
            if self._api_key and self._api_secret:
                try:
                    await self.get_account()
                except AuthenticationError:
                    raise
                except Exception:
                    # Other errors are okay - just means account might not have UTA
                    pass
            
            self._connected = True
            return True
            
        except Exception as e:
            if isinstance(e, AuthenticationError):
                raise
            raise ExchangeConnectionError(f"Failed to connect to Bybit: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Bybit and cleanup resources."""
        # Close all WebSocket connections
        await self._close_all_websockets()
        
        # Close HTTP client
        await self._client.aclose()
        self._connected = False
    
    # ==================== Public Market Data ====================
    
    async def get_server_time(self) -> int:
        """Get Bybit server time.
        
        Returns:
            Server timestamp in milliseconds
        """
        response = await self._make_request("GET", "/v5/market/time")
        return response.get("result", {}).get("timeSecond", 0) * 1000
    
    async def get_ticker(self, symbol: str, category: str = "linear") -> Ticker:
        """Get 24-hour ticker data for a symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            category: Product type (spot, linear, inverse, option)
            
        Returns:
            Ticker object with price and volume data
        """
        params = {
            "category": category,
            "symbol": symbol
        }
        
        response = await self._make_request("GET", "/v5/market/tickers", params)
        result = response.get("result", {}).get("list", [{}])[0]
        
        return Ticker(
            symbol=symbol,
            bid=Decimal(result.get("bid1Price", 0)),
            ask=Decimal(result.get("ask1Price", 0)),
            last=Decimal(result.get("lastPrice", 0)),
            volume=Decimal(result.get("volume24h", 0)),
            timestamp=time.time(),
            high_24h=Decimal(result.get("highPrice24h", 0)) if result.get("highPrice24h") else None,
            low_24h=Decimal(result.get("lowPrice24h", 0)) if result.get("lowPrice24h") else None
        )
    
    async def get_orderbook(self, symbol: str, category: str = "linear", limit: int = 25) -> OrderBook:
        """Get L2 orderbook for a symbol.
        
        Args:
            symbol: Trading pair symbol
            category: Product type (spot, linear, inverse, option)
            limit: Number of levels (spot: 1-200, linear/inverse: 1-500, option: 1-25)
            
        Returns:
            OrderBook object with bids and asks
        """
        params = {
            "category": category,
            "symbol": symbol,
            "limit": limit
        }
        
        response = await self._make_request("GET", "/v5/market/orderbook", params)
        result = response.get("result", {})
        
        bids = [[Decimal(price), Decimal(qty)] for price, qty in result.get("b", [])]
        asks = [[Decimal(price), Decimal(qty)] for price, qty in result.get("a", [])]
        
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=result.get("ts", int(time.time() * 1000)) / 1000,
            sequence=result.get("u")
        )
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        category: str = "linear",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200
    ) -> List[Candle]:
        """Get historical OHLCV candle data.
        
        Args:
            symbol: Trading pair symbol
            interval: Candle interval (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
            category: Product type
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Maximum number of candles (max 1000)
            
        Returns:
            List of Candle objects
        """
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        
        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time
        
        response = await self._make_request("GET", "/v5/market/kline", params)
        result = response.get("result", {})
        
        candles = []
        for item in result.get("list", []):
            # Bybit returns: [timestamp, open, high, low, close, volume, turnover]
            candles.append(Candle(
                timestamp=int(item[0]),
                open=Decimal(item[1]),
                high=Decimal(item[2]),
                low=Decimal(item[3]),
                close=Decimal(item[4]),
                volume=Decimal(item[5])
            ))
        
        return candles
    
    async def get_funding_rate(
        self,
        symbol: str,
        category: str = "linear",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Get funding rate history for a perpetual contract.
        
        Args:
            symbol: Trading pair symbol
            category: Product type (linear or inverse)
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Maximum number of records
            
        Returns:
            List of funding rate records
        """
        params = {
            "category": category,
            "symbol": symbol,
            "limit": min(limit, 1000)
        }
        
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        response = await self._make_request("GET", "/v5/market/funding/history", params)
        result = response.get("result", {})
        
        funding_rates = []
        for item in result.get("list", []):
            funding_rates.append({
                "symbol": item.get("symbol"),
                "fundingRate": Decimal(item.get("fundingRate", 0)),
                "fundingRateTimestamp": int(item.get("fundingRateTimestamp", 0)),
            })
        
        return funding_rates
    
    async def get_open_interest(
        self,
        symbol: str,
        interval: str = "1h",
        category: str = "linear",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Get open interest data.
        
        Args:
            symbol: Trading pair symbol
            interval: Data interval (5min, 15min, 30min, 1h, 4h, 1d)
            category: Product type
            start_time: Start timestamp (ms)
            end_time: End timestamp (ms)
            limit: Maximum number of records
            
        Returns:
            List of open interest records
        """
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        response = await self._make_request("GET", "/v5/market/open-interest", params)
        result = response.get("result", {})
        
        oi_data = []
        for item in result.get("list", []):
            oi_data.append({
                "symbol": item.get("symbol"),
                "openInterest": Decimal(item.get("openInterest", 0)),
                "timestamp": int(item.get("timestamp", 0))
            })
        
        return oi_data
    
    async def get_recent_trades(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get recent public trades.
        
        Args:
            symbol: Trading pair symbol
            category: Product type
            limit: Number of trades (spot: max 60, others: max 1000)
            
        Returns:
            List of trade records
        """
        params = {
            "category": category,
            "symbol": symbol,
            "limit": limit
        }
        
        response = await self._make_request("GET", "/v5/market/recent-trade", params)
        result = response.get("result", {})
        
        trades = []
        for item in result.get("list", []):
            trades.append({
                "execId": item.get("execId"),
                "symbol": item.get("symbol"),
                "price": Decimal(item.get("price", 0)),
                "size": Decimal(item.get("size", 0)),
                "side": item.get("side"),
                "time": int(item.get("time", 0)),
                "isBlockTrade": item.get("isBlockTrade", False)
            })
        
        return trades
    
    # ==================== Account & Wallet ====================
    
    async def get_account(self) -> AccountInfo:
        """Get account information for Unified Trading Account.
        
        Returns:
            AccountInfo object with account details
            
        Note:
            This returns Unified Trading Account (UTA) information.
            Use get_balances() for detailed wallet information.
        """
        response = await self._make_request("GET", "/v5/account/info", signed=True)
        result = response.get("result", {})
        
        return AccountInfo(
            account_id=str(result.get("uid", "")),
            account_type="UNIFIED",
            permissions=result.get("permissions", []),
            created_at=None
        )
    
    async def get_balances(self, coin: Optional[str] = None) -> List[Balance]:
        """Get wallet balances for Unified Trading Account.
        
        Args:
            coin: Specific coin to query (optional). Can be comma-separated for multiple coins.
            
        Returns:
            List of Balance objects
        """
        params = {
            "accountType": "UNIFIED"
        }
        if coin:
            params["coin"] = coin
        
        response = await self._make_request("GET", "/v5/account/wallet-balance", params, signed=True)
        result = response.get("result", {})
        
        balances = []
        for account in result.get("list", []):
            for coin_data in account.get("coin", []):
                wallet_balance = Decimal(coin_data.get("walletBalance", 0))
                locked = Decimal(coin_data.get("locked", 0))
                
                if wallet_balance > 0 or locked > 0:
                    balances.append(Balance(
                        asset=coin_data.get("coin", ""),
                        free=Decimal(coin_data.get("availableToWithdraw", wallet_balance)),
                        locked=locked,
                        total=wallet_balance
                    ))
        
        return balances
    
    # ==================== Position Management ====================
    
    async def get_positions(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        settle_coin: Optional[str] = None
    ) -> List[Position]:
        """Get open positions.
        
        Args:
            category: Product type (linear, inverse, option)
            symbol: Symbol to filter by (optional)
            settle_coin: Settle coin to filter by (optional)
            
        Returns:
            List of Position objects
        """
        params = {
            "category": category
        }
        if symbol:
            params["symbol"] = symbol
        if settle_coin:
            params["settleCoin"] = settle_coin
        
        response = await self._make_request("GET", "/v5/position/list", params, signed=True)
        result = response.get("result", {})
        
        positions = []
        for pos in result.get("list", []):
            size = Decimal(pos.get("size", 0))
            if size == 0:
                continue
                
            side = pos.get("side", "")
            quantity = size if side == "Buy" else -size
            
            positions.append(Position(
                symbol=pos.get("symbol", ""),
                quantity=quantity,
                avg_entry_price=Decimal(pos.get("avgPrice", 0)),
                current_price=Decimal(pos.get("markPrice", 0)),
                unrealized_pnl=Decimal(pos.get("unrealisedPnl", 0)),
                realized_pnl=Decimal(pos.get("cumRealisedPnl", 0)),
                leverage=Decimal(pos.get("leverage", 1)),
                margin_mode="cross" if pos.get("tradeMode") == 0 else "isolated"
            ))
        
        return positions
    
    async def set_leverage(
        self,
        category: str,
        symbol: str,
        buy_leverage: Union[str, int, float],
        sell_leverage: Union[str, int, float]
    ) -> bool:
        """Set leverage for a symbol.
        
        Args:
            category: Product type (linear, inverse)
            symbol: Trading pair symbol
            buy_leverage: Leverage for buy side (1 to max leverage)
            sell_leverage: Leverage for sell side
            
        Returns:
            True if successful
        """
        data = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(buy_leverage),
            "sellLeverage": str(sell_leverage)
        }
        
        response = await self._make_request("POST", "/v5/position/set-leverage", data=data, signed=True)
        return response.get("retCode") == 0
    
    # ==================== Order Management ====================
    
    def _convert_order_type(self, order_type: OrderType) -> str:
        """Convert internal OrderType to Bybit format."""
        mapping = {
            OrderType.MARKET: "Market",
            OrderType.LIMIT: "Limit",
        }
        return mapping.get(order_type, "Limit")
    
    def _convert_time_in_force(self, tif: TimeInForce) -> str:
        """Convert internal TimeInForce to Bybit format."""
        mapping = {
            TimeInForce.GTC: "GTC",
            TimeInForce.IOC: "IOC",
            TimeInForce.FOK: "FOK"
        }
        return mapping.get(tif, "GTC")
    
    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse Bybit order status to internal OrderStatus."""
        mapping = {
            "New": OrderStatus.OPEN,
            "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
            "Filled": OrderStatus.FILLED,
            "Cancelled": OrderStatus.CANCELLED,
            "Rejected": OrderStatus.REJECTED,
            "Triggered": OrderStatus.OPEN,
            "Active": OrderStatus.OPEN,
        }
        return mapping.get(status, OrderStatus.PENDING)
    
    async def place_order(
        self,
        order: Order,
        category: str = "linear",
        **kwargs
    ) -> Order:
        """Place a new order on Bybit.
        
        Args:
            order: Order object with trade details
            category: Product type (spot, linear, inverse, option)
            **kwargs: Additional Bybit-specific parameters:
                - is_leverage: bool - Whether to borrow for spot margin
                - order_link_id: str - Client order ID
                - reduce_only: bool - Reduce only flag
                - close_on_trigger: bool - Close on trigger
                - trigger_price: str - Trigger price for conditional orders
                - trigger_direction: int - 1: rise, 2: fall
                - take_profit: str - Take profit price
                - stop_loss: str - Stop loss price
                - tp_sl_mode: str - "Full" or "Partial"
                - tp_trigger_by: str - Trigger type for TP
                - sl_trigger_by: str - Trigger type for SL
                - position_idx: int - 0: one-way, 1: buy hedge, 2: sell hedge
                
        Returns:
            Updated Order object with exchange order_id and status
        """
        data = {
            "category": category,
            "symbol": order.symbol,
            "side": "Buy" if order.side == OrderSide.BUY else "Sell",
            "orderType": self._convert_order_type(order.order_type),
            "qty": str(order.quantity)
        }
        
        # Add price for limit orders
        if order.order_type == OrderType.LIMIT and order.price:
            data["price"] = str(order.price)
            data["timeInForce"] = self._convert_time_in_force(order.time_in_force)
        
        # Add optional parameters
        if order.client_order_id:
            data["orderLinkId"] = order.client_order_id
        
        # Bybit-specific parameters
        if "is_leverage" in kwargs:
            data["isLeverage"] = 1 if kwargs["is_leverage"] else 0
        
        if "reduce_only" in kwargs:
            data["reduceOnly"] = kwargs["reduce_only"]
        
        if "close_on_trigger" in kwargs:
            data["closeOnTrigger"] = kwargs["close_on_trigger"]
        
        if "trigger_price" in kwargs:
            data["triggerPrice"] = str(kwargs["trigger_price"])
        
        if "trigger_direction" in kwargs:
            data["triggerDirection"] = kwargs["trigger_direction"]
        
        if "take_profit" in kwargs:
            data["takeProfit"] = str(kwargs["take_profit"])
        
        if "stop_loss" in kwargs:
            data["stopLoss"] = str(kwargs["stop_loss"])
        
        if "tp_sl_mode" in kwargs:
            data["tpslMode"] = kwargs["tp_sl_mode"]
        
        if "tp_trigger_by" in kwargs:
            data["tpTriggerBy"] = kwargs["tp_trigger_by"]
        
        if "sl_trigger_by" in kwargs:
            data["slTriggerBy"] = kwargs["sl_trigger_by"]
        
        if "position_idx" in kwargs:
            data["positionIdx"] = kwargs["position_idx"]
        
        response = await self._make_request("POST", "/v5/order/create", data=data, signed=True)
        result = response.get("result", {})
        
        # Update order with response data
        order.order_id = result.get("orderId")
        order.client_order_id = result.get("orderLinkId")
        order.created_at = time.time()
        
        return order
    
    async def cancel_order(
        self,
        symbol: str,
        category: str = "linear",
        order_id: Optional[str] = None,
        order_link_id: Optional[str] = None
    ) -> bool:
        """Cancel an existing order.
        
        Args:
            symbol: Trading pair symbol
            category: Product type
            order_id: Exchange order ID (either order_id or order_link_id required)
            order_link_id: Client order ID
            
        Returns:
            True if cancellation successful
        """
        if not order_id and not order_link_id:
            raise ValueError("Either order_id or order_link_id must be provided")
        
        data = {
            "category": category,
            "symbol": symbol
        }
        
        if order_id:
            data["orderId"] = order_id
        if order_link_id:
            data["orderLinkId"] = order_link_id
        
        response = await self._make_request("POST", "/v5/order/cancel", data=data, signed=True)
        return response.get("retCode") == 0
    
    async def get_order_status(
        self,
        symbol: str,
        category: str = "linear",
        order_id: Optional[str] = None,
        order_link_id: Optional[str] = None
    ) -> Order:
        """Get order status by ID.
        
        Args:
            symbol: Trading pair symbol
            category: Product type
            order_id: Exchange order ID
            order_link_id: Client order ID
            
        Returns:
            Order object with current status
        """
        params = {
            "category": category,
            "symbol": symbol
        }
        
        if order_id:
            params["orderId"] = order_id
        if order_link_id:
            params["orderLinkId"] = order_link_id
        
        response = await self._make_request("GET", "/v5/order/realtime", params, signed=True)
        result = response.get("result", {})
        order_list = result.get("list", [])
        
        if not order_list:
            raise OrderError(f"Order not found: {order_id or order_link_id}")
        
        return self._parse_order_data(order_list[0])
    
    def _parse_order_data(self, data: Dict[str, Any]) -> Order:
        """Parse Bybit order data to Order object."""
        price = Decimal(data.get("price", 0)) if data.get("price") else None
        avg_price = Decimal(data.get("avgPrice", 0)) if data.get("avgPrice") else None
        
        # Calculate filled quantity
        cum_exec_qty = Decimal(data.get("cumExecQty", 0))
        
        return Order(
            symbol=data.get("symbol", ""),
            side=OrderSide.BUY if data.get("side") == "Buy" else OrderSide.SELL,
            order_type=OrderType.LIMIT if data.get("orderType") == "Limit" else OrderType.MARKET,
            quantity=Decimal(data.get("qty", 0)),
            price=price,
            order_id=data.get("orderId"),
            client_order_id=data.get("orderLinkId"),
            status=self._parse_order_status(data.get("orderStatus", "")),
            filled_quantity=cum_exec_qty,
            avg_fill_price=avg_price if avg_price and avg_price > 0 else None,
            created_at=int(data.get("createdTime", 0)) / 1000 if data.get("createdTime") else None
        )
    
    async def get_open_orders(
        self,
        category: str = "linear",
        symbol: Optional[str] = None,
        settle_coin: Optional[str] = None,
        order_filter: Optional[str] = None
    ) -> List[Order]:
        """Get all open orders.
        
        Args:
            category: Product type
            symbol: Symbol filter (optional)
            settle_coin: Settle coin filter (optional)
            order_filter: Order filter type (Order, StopOrder, tpslOrder, etc.)
            
        Returns:
            List of open Order objects
        """
        params = {
            "category": category,
            "openOnly": 0  # Query open orders only
        }
        
        if symbol:
            params["symbol"] = symbol
        if settle_coin:
            params["settleCoin"] = settle_coin
        if order_filter:
            params["orderFilter"] = order_filter
        
        response = await self._make_request("GET", "/v5/order/realtime", params, signed=True)
        result = response.get("result", {})
        
        return [self._parse_order_data(order) for order in result.get("list", [])]
    
    # ==================== WebSocket Methods ====================
    
    async def _ws_connect(
        self,
        url: str,
        callback: Callable[[Dict[str, Any]], None],
        is_private: bool = False
    ) -> websockets.WebSocketClientProtocol:
        """Connect to WebSocket with optional authentication.
        
        Args:
            url: WebSocket URL
            callback: Callback function for messages
            is_private: Whether this is a private stream requiring auth
            
        Returns:
            WebSocket client protocol
        """
        max_reconnect_attempts = 10
        base_delay = 1.0
        max_delay = 60.0
        
        ws_id = f"{url}_{is_private}"
        self._ws_running[ws_id] = True
        
        while self._ws_running.get(ws_id, False):
            try:
                self._logger.info(f"Connecting to WebSocket: {url}")
                
                async with websockets.connect(url) as ws:
                    self._ws_connections[ws_id] = ws
                    self._ws_reconnect_attempts[ws_id] = 0
                    
                    # Authenticate for private streams
                    if is_private:
                        auth_msg = self._get_ws_auth_message()
                        await ws.send(json.dumps(auth_msg))
                        
                        # Wait for auth response
                        auth_response = await ws.recv()
                        auth_data = json.loads(auth_response)
                        if auth_data.get("success") is False:
                            raise AuthenticationError(f"WebSocket auth failed: {auth_data}")
                    
                    self._logger.info(f"WebSocket connected: {url}")
                    
                    # Resubscribe to any existing subscriptions
                    if ws_id in self._ws_subscriptions:
                        for sub_msg in self._ws_subscriptions[ws_id]:
                            await ws.send(sub_msg)
                    
                    # Listen for messages
                    async for message in ws:
                        if not self._ws_running.get(ws_id, False):
                            break
                        
                        try:
                            data = json.loads(message)
                            
                            # Skip heartbeat/ping messages
                            if "op" in data and data["op"] in ["ping", "pong"]:
                                continue
                            
                            await callback(data)
                        except Exception as e:
                            self._logger.error(f"Error processing WebSocket message: {e}")
                            
            except (ConnectionClosedError, ConnectionClosedOK) as e:
                self._logger.warning(f"WebSocket closed: {url} - {e}")
            except Exception as e:
                self._logger.error(f"WebSocket error: {url} - {e}")
            
            # Reconnection logic
            if self._ws_running.get(ws_id, False):
                attempts = self._ws_reconnect_attempts.get(ws_id, 0)
                self._ws_reconnect_attempts[ws_id] = attempts + 1
                
                if attempts >= max_reconnect_attempts:
                    self._logger.error(f"Max reconnection attempts reached for {url}")
                    break
                
                delay = min(base_delay * (2 ** attempts), max_delay)
                jitter = random.uniform(0, 0.1 * delay)
                total_delay = delay + jitter
                
                self._logger.info(f"Reconnecting to {url} in {total_delay:.1f}s (attempt {attempts + 1})")
                await asyncio.sleep(total_delay)
        
        self._logger.info(f"WebSocket handler stopped: {url}")
    
    def _get_ws_auth_message(self) -> Dict[str, Any]:
        """Generate WebSocket authentication message."""
        timestamp = str(int(time.time() * 1000))
        recv_window = str(self.recv_window)
        
        # Generate signature
        param_str = timestamp + self._api_key + recv_window
        signature = hmac.new(
            bytes(self._api_secret, "utf-8"),
            bytes(param_str, "utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        return {
            "op": "auth",
            "args": [self._api_key, timestamp, signature]
        }
    
    def _get_category_ws_url(self, category: str) -> str:
        """Get WebSocket URL for a category."""
        base_url = self._ws_public_url
        
        if category == BybitCategory.SPOT:
            return f"{base_url}/{self.WS_SPOT_PATH}"
        elif category == BybitCategory.LINEAR:
            return f"{base_url}/{self.WS_LINEAR_PATH}"
        elif category == BybitCategory.INVERSE:
            return f"{base_url}/{self.WS_INVERSE_PATH}"
        elif category == BybitCategory.OPTION:
            return f"{base_url}/{self.WS_OPTION_PATH}"
        else:
            return f"{base_url}/{category}"
    
    async def subscribe_to_tickers(
        self,
        symbols: List[str],
        category: str = "linear",
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> str:
        """Subscribe to real-time ticker updates.
        
        Args:
            symbols: List of trading symbols
            category: Product type
            callback: Callback function for ticker updates
            
        Returns:
            Subscription ID
        """
        ws_url = self._get_category_ws_url(category)
        
        subscription_id = f"tickers_{category}_{'_'.join(symbols)}"
        
        async def _on_message(data: Dict[str, Any]):
            if callback:
                await callback(data)
        
        # Build subscription message
        args = [f"tickers.{symbol}" for symbol in symbols]
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        
        # Start connection
        task = asyncio.create_task(self._ws_connect(ws_url, _on_message, is_private=False))
        self._ws_tasks[subscription_id] = task
        
        # Store subscription for reconnection
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        # Wait a bit for connection then send subscription
        await asyncio.sleep(0.5)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_orderbook(
        self,
        symbols: List[str],
        category: str = "linear",
        depth: int = 50,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> str:
        """Subscribe to real-time orderbook updates.
        
        Args:
            symbols: List of trading symbols
            category: Product type
            depth: Orderbook depth (1-500 for linear/inverse, 1-200 for spot)
            callback: Callback function for orderbook updates
            
        Returns:
            Subscription ID
        """
        ws_url = self._get_category_ws_url(category)
        subscription_id = f"orderbook_{category}_{depth}_{'_'.join(symbols)}"
        
        async def _on_message(data: Dict[str, Any]):
            if callback:
                await callback(data)
        
        # Build subscription message
        args = [f"orderbook.{depth}.{symbol}" for symbol in symbols]
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        
        task = asyncio.create_task(self._ws_connect(ws_url, _on_message, is_private=False))
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(0.5)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_trades(
        self,
        symbols: List[str],
        category: str = "linear",
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> str:
        """Subscribe to real-time public trade stream.
        
        Args:
            symbols: List of trading symbols
            category: Product type
            callback: Callback function for trade updates
            
        Returns:
            Subscription ID
        """
        ws_url = self._get_category_ws_url(category)
        subscription_id = f"trades_{category}_{'_'.join(symbols)}"
        
        async def _on_message(data: Dict[str, Any]):
            if callback:
                await callback(data)
        
        args = [f"publicTrade.{symbol}" for symbol in symbols]
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        
        task = asyncio.create_task(self._ws_connect(ws_url, _on_message, is_private=False))
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(0.5)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_klines(
        self,
        symbols: List[str],
        interval: str,
        category: str = "linear",
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> str:
        """Subscribe to real-time kline/candle updates.
        
        Args:
            symbols: List of trading symbols
            interval: Kline interval (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
            category: Product type
            callback: Callback function for kline updates
            
        Returns:
            Subscription ID
        """
        ws_url = self._get_category_ws_url(category)
        subscription_id = f"klines_{category}_{interval}_{'_'.join(symbols)}"
        
        async def _on_message(data: Dict[str, Any]):
            if callback:
                await callback(data)
        
        args = [f"kline.{interval}.{symbol}" for symbol in symbols]
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        
        task = asyncio.create_task(self._ws_connect(ws_url, _on_message, is_private=False))
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(0.5)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_position(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> str:
        """Subscribe to position updates (private).
        
        Args:
            callback: Callback function for position updates
            
        Returns:
            Subscription ID
        """
        subscription_id = "private_position"
        
        async def _on_message(data: Dict[str, Any]):
            await callback(data)
        
        sub_msg = {
            "op": "subscribe",
            "args": ["position"]
        }
        
        task = asyncio.create_task(
            self._ws_connect(self._ws_private_url, _on_message, is_private=True)
        )
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(1.0)  # Wait longer for auth
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_execution(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> str:
        """Subscribe to order execution updates (private).
        
        Args:
            callback: Callback function for execution updates
            
        Returns:
            Subscription ID
        """
        subscription_id = "private_execution"
        
        async def _on_message(data: Dict[str, Any]):
            await callback(data)
        
        sub_msg = {
            "op": "subscribe",
            "args": ["execution"]
        }
        
        task = asyncio.create_task(
            self._ws_connect(self._ws_private_url, _on_message, is_private=True)
        )
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(1.0)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_to_wallet(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> str:
        """Subscribe to wallet/balance updates (private).
        
        Args:
            callback: Callback function for wallet updates
            
        Returns:
            Subscription ID
        """
        subscription_id = "private_wallet"
        
        async def _on_message(data: Dict[str, Any]):
            await callback(data)
        
        sub_msg = {
            "op": "subscribe",
            "args": ["wallet"]
        }
        
        task = asyncio.create_task(
            self._ws_connect(self._ws_private_url, _on_message, is_private=True)
        )
        self._ws_tasks[subscription_id] = task
        
        if subscription_id not in self._ws_subscriptions:
            self._ws_subscriptions[subscription_id] = set()
        self._ws_subscriptions[subscription_id].add(json.dumps(sub_msg))
        
        await asyncio.sleep(1.0)
        if subscription_id in self._ws_connections:
            await self._ws_connections[subscription_id].send(json.dumps(sub_msg))
        
        return subscription_id
    
    async def subscribe_market_data(
        self,
        symbols: List[str],
        channels: List[str],
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to real-time market data via WebSocket (base adapter method).
        
        Args:
            symbols: List of trading symbols
            channels: Data channels (ticker, orderbook, trade, kline)
            callback: Callback function
        """
        for channel in channels:
            if channel == "ticker":
                await self.subscribe_to_tickers(symbols, callback=callback)
            elif channel == "orderbook":
                await self.subscribe_to_orderbook(symbols, callback=callback)
            elif channel == "trade":
                await self.subscribe_to_trades(symbols, callback=callback)
            elif channel == "kline":
                # Default to 1m interval
                await self.subscribe_to_klines(symbols, "1", callback=callback)
    
    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a WebSocket stream.
        
        Args:
            subscription_id: Subscription ID returned from subscribe method
        """
        self._ws_running[subscription_id] = False
        
        # Cancel task
        if subscription_id in self._ws_tasks:
            task = self._ws_tasks[subscription_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self._ws_tasks[subscription_id]
        
        # Close connection
        if subscription_id in self._ws_connections:
            try:
                await self._ws_connections[subscription_id].close()
            except Exception:
                pass
            del self._ws_connections[subscription_id]
        
        # Clean up subscriptions
        if subscription_id in self._ws_subscriptions:
            del self._ws_subscriptions[subscription_id]
        
        if subscription_id in self._ws_reconnect_attempts:
            del self._ws_reconnect_attempts[subscription_id]
    
    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all WebSocket streams."""
        subscription_ids = list(self._ws_running.keys())
        for sub_id in subscription_ids:
            await self.unsubscribe(sub_id)
    
    async def _close_all_websockets(self) -> None:
        """Close all WebSocket connections."""
        await self.unsubscribe_all()
    
    # ==================== Utility Methods ====================
    
    async def ping(self) -> bool:
        """Test connectivity to Bybit API.
        
        Returns:
            True if connection is successful
        """
        try:
            await self.get_server_time()
            return True
        except Exception:
            return False
    
    def get_symbols(self, category: str = "linear") -> List[str]:
        """Get list of available trading symbols.
        
        This is a synchronous helper - use get_instruments_info for async call.
        
        Args:
            category: Product type
            
        Returns:
            List of symbol names
        """
        # Note: This is a placeholder. In practice, you'd cache instrument info
        # or provide an async version
        return []
    
    async def get_instruments_info(
        self,
        category: str = "linear",
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get instrument specifications.
        
        Args:
            category: Product type
            symbol: Specific symbol (optional)
            
        Returns:
            List of instrument information
        """
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        
        response = await self._make_request("GET", "/v5/market/instruments-info", params)
        result = response.get("result", {})
        
        return result.get("list", [])


# For testing
if __name__ == "__main__":
    async def test_adapter():
        """Test the Bybit adapter."""
        adapter = BybitAdapter(
            api_key="test_key",
            api_secret="test_secret",
            sandbox=True
        )
        
        try:
            # Test connectivity
            print("Testing connectivity...")
            if await adapter.ping():
                print("✓ Ping successful")
            
            # Get server time
            server_time = await adapter.get_server_time()
            print(f"✓ Server time: {server_time}")
            
            # Get ticker
            print("\nGetting BTCUSDT ticker...")
            ticker = await adapter.get_ticker("BTCUSDT", category="linear")
            print(f"✓ Last price: {ticker.last}")
            print(f"✓ Bid: {ticker.bid}, Ask: {ticker.ask}")
            
            # Get orderbook
            print("\nGetting orderbook...")
            orderbook = await adapter.get_orderbook("BTCUSDT", category="linear", limit=5)
            print(f"✓ Best bid: {orderbook.best_bid}")
            print(f"✓ Best ask: {orderbook.best_ask}")
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await adapter.disconnect()
    
    # Run test
    asyncio.run(test_adapter())
