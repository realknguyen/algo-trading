"""Binance exchange adapter implementation.

This module provides a comprehensive adapter for the Binance cryptocurrency exchange,
supporting both REST API and WebSocket connections with auto-reconnect capabilities.

Features:
    - HMAC SHA256 request signing
    - recvWindow parameter handling
    - Testnet support toggle
    - Weight-based rate limit handling
    - WebSocket auto-reconnect with exponential backoff
    - Support for spot and futures trading

Reference: https://binance-docs.github.io/apidocs/spot/en/
"""

import asyncio
import hashlib
import hmac
import json
import random
import time
from decimal import Decimal
from typing import Optional, Dict, Any, List, Callable, Set
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
        ExchangeError, AuthenticationError, InsufficientFundsError, InvalidSymbolError
    )
except ImportError:
    # Fallback for when running from src/adapters directly
    from base_adapter import (
        BaseExchangeAdapter, Order, OrderType, OrderSide, 
        OrderStatus, TimeInForce, Ticker, Position, Balance,
        ExchangeError, AuthenticationError, InsufficientFundsError, InvalidSymbolError
    )


class BinanceRateLimitError(ExchangeError):
    """Raised when rate limit is exceeded."""
    pass


class BinanceAPIError(ExchangeError):
    """Raised when Binance API returns an error."""
    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.code = code


class BinanceAdapter(BaseExchangeAdapter):
    """Binance exchange adapter with full REST and WebSocket support.
    
    Supports both spot and futures trading with testnet support.
    Implements auto-reconnect for WebSocket connections and proper
    rate limit handling.
    
    Args:
        api_key: Binance API key
        api_secret: Binance API secret
        sandbox: Use testnet (default: True)
        futures: Use futures API (default: False)
        rate_limit_per_second: Requests per second limit (default: 10.0)
        recv_window: Request validity window in milliseconds (default: 5000)
    """
    
    # REST API Endpoints
    SPOT_BASE_URL = "https://api.binance.com"
    SPOT_TESTNET_URL = "https://testnet.binance.vision"
    FUTURES_BASE_URL = "https://fapi.binance.com"
    FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"
    
    # WebSocket Endpoints
    WS_SPOT_URL = "wss://stream.binance.com:9443/ws"
    WS_SPOT_STREAM_URL = "wss://stream.binance.com:9443/stream"
    WS_TESTNET_URL = "wss://testnet.binance.vision/ws"
    WS_TESTNET_STREAM_URL = "wss://testnet.binance.vision/stream"
    WS_FUTURES_URL = "wss://fstream.binance.com/ws"
    WS_FUTURES_TESTNET_URL = "wss://stream.binancefuture.com/ws"
    
    # API Weights (for rate limit tracking)
    WEIGHTS = {
        'ping': 1,
        'time': 1,
        'exchangeInfo': 10,
        'depth': {'default': 1, '100': 1, '500': 5, '1000': 10},
        'trades': 1,
        'historicalTrades': 5,
        'aggTrades': 1,
        'klines': 1,
        'avgPrice': 1,
        'ticker_24hr': {'default': 1, 'all': 40},
        'ticker_price': {'default': 1, 'all': 2},
        'ticker_bookTicker': {'default': 1, 'all': 2},
        'order': {'post': 1, 'get': 1, 'delete': 1},
        'openOrders': {'get': 3, 'delete': 1},
        'allOrders': 5,
        'ocoOrder': {'post': 1, 'get': 1, 'delete': 1},
        'allOrderList': 3,
        'openOrderList': 3,
        'account': 10,
        'myTrades': 5,
    }
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        futures: bool = False,
        rate_limit_per_second: float = 10.0,
        recv_window: int = 5000,
        **kwargs
    ):
        self.futures = futures
        self.recv_window = recv_window
        self._current_weight = 0
        self._weight_reset_time = time.time()
        self._weight_lock = asyncio.Lock()
        
        # WebSocket state
        self._ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._ws_callbacks: Dict[str, Callable] = {}
        self._ws_reconnect_attempts: Dict[str, int] = {}
        self._ws_running: Dict[str, bool] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        
        # Select appropriate base URL
        if futures:
            base_url = self.FUTURES_TESTNET_URL if sandbox else self.FUTURES_BASE_URL
            ws_url = self.WS_FUTURES_TESTNET_URL if sandbox else self.WS_FUTURES_URL
            ws_stream_url = ws_url.replace('/ws', '/stream')
        else:
            base_url = self.SPOT_TESTNET_URL if sandbox else self.SPOT_BASE_URL
            ws_url = self.WS_TESTNET_URL if sandbox else self.WS_SPOT_URL
            ws_stream_url = self.WS_TESTNET_STREAM_URL if sandbox else self.WS_SPOT_STREAM_URL
        
        self._ws_stream_url = ws_stream_url
        
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            ws_url=ws_url,
            **kwargs
        )
    
    def _get_weight(self, endpoint: str, method: str = 'GET', **kwargs) -> int:
        """Get the weight for a specific endpoint.
        
        Args:
            endpoint: API endpoint
            method: HTTP method
            **kwargs: Additional parameters affecting weight
            
        Returns:
            Weight value for rate limit tracking
        """
        # Extract key from endpoint
        key = endpoint.strip('/').split('/')[-1].split('?')[0]
        
        if key not in self.WEIGHTS:
            return 1
        
        weight = self.WEIGHTS[key]
        
        # Handle nested weights
        if isinstance(weight, dict):
            # Check method-specific weight
            method_lower = method.lower()
            if method_lower in weight:
                return weight[method_lower]
            
            # Check for 'all' vs specific symbol
            if kwargs.get('symbol'):
                return weight.get('default', 1)
            else:
                return weight.get('all', weight.get('default', 1))
        
        return weight
    
    async def _track_weight(self, weight: int) -> None:
        """Track API weight usage with 1-minute window."""
        async with self._weight_lock:
            now = time.time()
            
            # Reset weight counter every minute
            if now - self._weight_reset_time >= 60:
                self._current_weight = 0
                self._weight_reset_time = now
            
            self._current_weight += weight
            
            # Binance allows 1200 weight per minute for most endpoints
            if self._current_weight > 1200:
                self.logger.logger.warning(
                    f"Approaching rate limit: {self._current_weight}/1200 weight used"
                )
    
    def _sign_request(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """Sign request with HMAC SHA256.
        
        Args:
            method: HTTP method (not used but kept for interface consistency)
            endpoint: API endpoint
            params: Query parameters to sign
            
        Returns:
            Headers dictionary with API key
        """
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        params['recvWindow'] = self.recv_window
        
        # Create query string from sorted parameters
        query_string = urlencode(sorted(params.items()))
        
        # Create HMAC SHA256 signature
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        params['signature'] = signature
        
        return {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    
    async def _authenticate(self) -> None:
        """Test authentication by getting account info."""
        try:
            await self.get_account()
            self.logger.logger.info("Binance authentication successful")
        except Exception as e:
            raise AuthenticationError(f"Binance authentication failed: {e}")
    
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
            data: Request body data
            signed: Whether to sign the request
            retries: Number of retries on failure
            
        Returns:
            JSON response as dictionary
            
        Raises:
            BinanceAPIError: If API returns an error
            BinanceRateLimitError: If rate limited
        """
        params = params or {}
        data = data or {}
        
        # Track weight before making request
        weight = self._get_weight(endpoint, method, **params)
        await self._track_weight(weight)
        
        async with self.rate_limiter:
            url = f"{self.base_url}{endpoint}"
            headers = {}
            
            if signed:
                headers = self._sign_request(method, endpoint, params)
            
            for attempt in range(retries):
                try:
                    if method.upper() == "GET":
                        response = await self.client.get(url, params=params, headers=headers)
                    elif method.upper() == "POST":
                        if signed:
                            # Signed POST uses query params with signature
                            query_string = urlencode(sorted(params.items()))
                            response = await self.client.post(
                                url, 
                                data=query_string,
                                headers=headers
                            )
                        else:
                            response = await self.client.post(url, json=data, headers=headers)
                    elif method.upper() == "DELETE":
                        response = await self.client.delete(url, params=params, headers=headers)
                    elif method.upper() == "PUT":
                        response = await self.client.put(url, json=data, headers=headers)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")
                    
                    # Check for Binance API errors
                    if response.status_code == 200:
                        result = response.json()
                        if isinstance(result, dict) and 'code' in result and result['code'] < 0:
                            raise BinanceAPIError(result.get('msg', 'Unknown error'), result['code'])
                        return result
                    
                    # Handle rate limiting
                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 1))
                        self.logger.logger.warning(f"Rate limited. Retry after {retry_after}s")
                        if attempt < retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise BinanceRateLimitError(f"Rate limit exceeded. Retry after {retry_after}s")
                    
                    # Handle IP ban
                    if response.status_code == 418:
                        retry_after = int(response.headers.get('Retry-After', 60))
                        self.logger.logger.error(f"IP banned. Retry after {retry_after}s")
                        raise BinanceRateLimitError(f"IP banned. Retry after {retry_after}s")
                    
                    response.raise_for_status()
                    return response.json()
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500 and attempt < retries - 1:
                        wait_time = 2 ** attempt
                        self.logger.logger.warning(f"Server error {e.response.status_code}, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    raise BinanceAPIError(f"HTTP error {e.response.status_code}: {e.response.text}")
                    
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    if attempt < retries - 1:
                        wait_time = 2 ** attempt
                        self.logger.logger.warning(f"Connection error, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    raise ExchangeError(f"Connection failed after {retries} attempts: {e}")
            
            raise ExchangeError(f"Request failed after {retries} attempts")
    
    # ==================== REST API Methods ====================
    
    async def get_account(self) -> Dict[str, Any]:
        """Get account information including balances and permissions.
        
        Returns:
            Dictionary containing account data
        """
        params = {}
        return await self._make_request("GET", "/api/v3/account", params=params, signed=True)
    
    async def get_balances(self) -> List[Balance]:
        """Get all non-zero account balances.
        
        Returns:
            List of Balance objects
        """
        account = await self.get_account()
        balances = []
        
        for asset in account.get('balances', []):
            free = Decimal(asset['free'])
            locked = Decimal(asset['locked'])
            
            if free > 0 or locked > 0:
                balances.append(Balance(
                    asset=asset['asset'],
                    free=free,
                    locked=locked,
                    total=free + locked
                ))
        
        return balances
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker/best price for symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            
        Returns:
            Ticker object with bid/ask/last prices
        """
        params = {'symbol': symbol.upper()}
        response = await self._make_request("GET", "/api/v3/ticker/bookTicker", params=params)
        
        return Ticker(
            symbol=symbol.upper(),
            bid=Decimal(response['bidPrice']),
            ask=Decimal(response['askPrice']),
            last=Decimal(response.get('lastPrice', response['bidPrice'])),
            volume=Decimal(response.get('volume', 0)),
            timestamp=time.time()
        )
    
    async def get_market_data(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get 24hr ticker price change statistics.
        
        Args:
            symbol: Trading pair symbol. If None, returns data for all symbols.
            
        Returns:
            Dictionary with 24hr statistics
        """
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        
        endpoint = "/api/v3/ticker/24hr"
        return await self._make_request("GET", endpoint, params=params)
    
    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get order book depth for symbol.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of bids/asks to return (5, 10, 20, 50, 100, 500, 1000, 5000)
            
        Returns:
            Dictionary with bids, asks, and lastUpdateId
        """
        # Ensure limit is valid
        valid_limits = [5, 10, 20, 50, 100, 500, 1000, 5000]
        if limit not in valid_limits:
            limit = min(valid_limits, key=lambda x: abs(x - limit))
        
        params = {
            'symbol': symbol.upper(),
            'limit': limit
        }
        response = await self._make_request("GET", "/api/v3/depth", params=params)
        
        return {
            'symbol': symbol.upper(),
            'bids': [[Decimal(p), Decimal(q)] for p, q in response['bids']],
            'asks': [[Decimal(p), Decimal(q)] for p, q in response['asks']],
            'lastUpdateId': response.get('lastUpdateId'),
            'timestamp': time.time()
        }
    
    def _convert_order_type(self, order_type: OrderType) -> str:
        """Convert internal OrderType to Binance format."""
        mapping = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP_LOSS: "STOP_LOSS",
            OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
            OrderType.TAKE_PROFIT: "TAKE_PROFIT",
            OrderType.TRAILING_STOP: "TRAILING_STOP_MARKET"
        }
        return mapping.get(order_type, "MARKET")
    
    def _convert_time_in_force(self, tif: TimeInForce) -> str:
        """Convert internal TimeInForce to Binance format."""
        mapping = {
            TimeInForce.GTC: "GTC",
            TimeInForce.IOC: "IOC",
            TimeInForce.FOK: "FOK"
        }
        return mapping.get(tif, "GTC")
    
    async def place_order(self, order: Order) -> Order:
        """Place a new order.
        
        Supports MARKET, LIMIT, STOP_LOSS, STOP_LIMIT, and TAKE_PROFIT order types.
        
        Args:
            order: Order object with trade details
            
        Returns:
            Updated Order object with response data
        """
        params = {
            'symbol': order.symbol.upper(),
            'side': order.side.value.upper(),
            'type': self._convert_order_type(order.order_type),
            'quantity': str(order.quantity)
        }
        
        # Add price for limit orders
        if order.order_type in [OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT]:
            params['timeInForce'] = self._convert_time_in_force(order.time_in_force)
            if order.price:
                params['price'] = str(order.price)
        
        # Add stop price for stop orders
        if order.stop_price and order.order_type in [
            OrderType.STOP_LOSS, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT
        ]:
            params['stopPrice'] = str(order.stop_price)
        
        # Add client order ID
        if order.client_order_id:
            params['newClientOrderId'] = order.client_order_id
        
        # For MARKET orders with quote quantity (e.g., buy BTC with specific USDT amount)
        if order.order_type == OrderType.MARKET and order.price and order.side == OrderSide.BUY:
            # Use quoteOrderQty instead of quantity
            del params['quantity']
            params['quoteOrderQty'] = str(order.price)
        
        response = await self._make_request(
            "POST", "/api/v3/order", params=params, signed=True
        )
        
        # Update order with response data
        order.order_id = str(response['orderId'])
        order.status = self._parse_order_status(response['status'])
        order.filled_quantity = Decimal(response.get('executedQty', 0))
        
        if response.get('avgPrice') and Decimal(response['avgPrice']) > 0:
            order.avg_fill_price = Decimal(response['avgPrice'])
        
        order.created_at = time.time()
        
        return order
    
    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse Binance order status to internal OrderStatus."""
        mapping = {
            'NEW': OrderStatus.OPEN,
            'PARTIALLY_FILLED': OrderStatus.PARTIALLY_FILLED,
            'FILLED': OrderStatus.FILLED,
            'CANCELED': OrderStatus.CANCELLED,
            'PENDING_CANCEL': OrderStatus.PENDING,
            'REJECTED': OrderStatus.REJECTED,
            'EXPIRED': OrderStatus.EXPIRED,
            'EXPIRED_IN_MATCH': OrderStatus.EXPIRED
        }
        return mapping.get(status, OrderStatus.PENDING)
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing order.
        
        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel
            
        Returns:
            True if cancellation was successful
        """
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        
        try:
            await self._make_request("DELETE", "/api/v3/order", params=params, signed=True)
            self.logger.logger.info(f"Order {order_id} cancelled successfully")
            return True
        except Exception as e:
            self.logger.error("cancel_order", f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def get_order_status(self, symbol: str, order_id: str) -> Order:
        """Get order status by ID.
        
        Args:
            symbol: Trading pair symbol
            order_id: Order ID to query
            
        Returns:
            Order object with current status
        """
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        
        response = await self._make_request("GET", "/api/v3/order", params=params, signed=True)
        
        return self._parse_order_response(response)
    
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Alias for get_order_status. Get order by ID."""
        return await self.get_order_status(symbol, order_id)
    
    def _parse_order_response(self, response: Dict[str, Any]) -> Order:
        """Parse Binance order response to Order object."""
        price = Decimal(response.get('price', 0)) if response.get('price') else None
        stop_price = Decimal(response.get('stopPrice', 0)) if response.get('stopPrice') else None
        avg_price = Decimal(response.get('avgPrice', 0)) if response.get('avgPrice') else None
        
        # Handle case where avgPrice is returned but is 0 or empty
        if avg_price and avg_price == 0:
            avg_price = None
        
        return Order(
            symbol=response['symbol'],
            side=OrderSide.BUY if response['side'] == 'BUY' else OrderSide.SELL,
            order_type=self._parse_order_type(response['type']),
            quantity=Decimal(response['origQty']),
            price=price,
            stop_price=stop_price,
            order_id=str(response['orderId']),
            status=self._parse_order_status(response['status']),
            filled_quantity=Decimal(response['executedQty']),
            avg_fill_price=avg_price,
            client_order_id=response.get('clientOrderId')
        )
    
    def _parse_order_type(self, order_type: str) -> OrderType:
        """Parse Binance order type to internal OrderType."""
        mapping = {
            'MARKET': OrderType.MARKET,
            'LIMIT': OrderType.LIMIT,
            'STOP_LOSS': OrderType.STOP_LOSS,
            'STOP_LOSS_LIMIT': OrderType.STOP_LIMIT,
            'TAKE_PROFIT': OrderType.TAKE_PROFIT,
            'TAKE_PROFIT_LIMIT': OrderType.TAKE_PROFIT,
            'LIMIT_MAKER': OrderType.LIMIT,
            'TRAILING_STOP_MARKET': OrderType.TRAILING_STOP
        }
        return mapping.get(order_type, OrderType.MARKET)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.
        
        Args:
            symbol: Optional symbol to filter by
            
        Returns:
            List of open Order objects
        """
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        
        response = await self._make_request("GET", "/api/v3/openOrders", params=params, signed=True)
        
        return [self._parse_order_response(data) for data in response]
    
    async def get_positions(self) -> List[Position]:
        """Get current positions.
        
        For spot trading, positions are derived from non-zero balances
        excluding stablecoins and quote currencies.
        
        Returns:
            List of Position objects
        """
        balances = await self.get_balances()
        positions = []
        
        # Common quote currencies to exclude
        quote_currencies = {'USDT', 'USDC', 'BUSD', 'TUSD', 'DAI', 'USD', 'EUR', 'GBP'}
        
        for balance in balances:
            # Skip quote currencies and zero balances
            if balance.asset in quote_currencies or balance.total <= 0:
                continue
            
            try:
                # Try to get ticker for asset against USDT
                trading_pair = f"{balance.asset}USDT"
                ticker = await self.get_ticker(trading_pair)
                
                positions.append(Position(
                    symbol=trading_pair,
                    quantity=balance.total,
                    avg_entry_price=ticker.last,  # Simplified - would need trade history for accurate cost basis
                    current_price=ticker.last,
                    unrealized_pnl=Decimal("0")  # Would need cost basis tracking
                ))
            except Exception:
                # Try BTC pair if USDT not available
                try:
                    trading_pair = f"{balance.asset}BTC"
                    ticker = await self.get_ticker(trading_pair)
                    positions.append(Position(
                        symbol=trading_pair,
                        quantity=balance.total,
                        avg_entry_price=ticker.last,
                        current_price=ticker.last,
                        unrealized_pnl=Decimal("0")
                    ))
                except Exception:
                    pass  # Asset not traded against USDT or BTC
        
        return positions
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get historical klines/candlestick data.
        
        Args:
            symbol: Trading pair symbol
            interval: Kline interval (1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M)
            start_time: Start time in milliseconds
            end_time: End time in milliseconds
            limit: Number of candles (max 1000)
            
        Returns:
            List of candle dictionaries with OHLCV data
        """
        params = {
            'symbol': symbol.upper(),
            'interval': interval,
            'limit': min(limit, 1000)
        }
        
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        
        response = await self._make_request("GET", "/api/v3/klines", params=params)
        
        candles = []
        for candle in response:
            candles.append({
                'timestamp': candle[0],
                'open_time': candle[0],
                'open': Decimal(candle[1]),
                'high': Decimal(candle[2]),
                'low': Decimal(candle[3]),
                'close': Decimal(candle[4]),
                'volume': Decimal(candle[5]),
                'close_time': candle[6],
                'quote_volume': Decimal(candle[7]),
                'trades': candle[8],
                'taker_buy_base_volume': Decimal(candle[9]),
                'taker_buy_quote_volume': Decimal(candle[10])
            })
        
        return candles
    
    # ==================== WebSocket Methods ====================
    
    async def _ws_connect_with_retry(
        self,
        stream_name: str,
        callback: Callable,
        is_combined: bool = False
    ) -> None:
        """Connect to WebSocket with exponential backoff retry.
        
        Args:
            stream_name: WebSocket stream name(s)
            callback: Function to call with message data
            is_combined: Whether this is a combined stream connection
        """
        max_reconnect_attempts = 10
        base_delay = 1.0
        max_delay = 60.0
        
        while self._ws_running.get(stream_name, False):
            try:
                # Build WebSocket URL
                if is_combined:
                    ws_url = f"{self._ws_stream_url}?streams={stream_name}"
                else:
                    ws_url = f"{self.ws_url}/{stream_name}"
                
                self.logger.logger.info(f"Connecting to WebSocket: {ws_url}")
                
                async with websockets.connect(ws_url) as ws:
                    self._ws_connections[stream_name] = ws
                    self._ws_reconnect_attempts[stream_name] = 0
                    
                    self.logger.logger.info(f"WebSocket connected: {stream_name}")
                    
                    # Listen for messages
                    async for message in ws:
                        if not self._ws_running.get(stream_name, False):
                            break
                        
                        try:
                            data = json.loads(message)
                            
                            # Handle combined stream format
                            if is_combined and 'stream' in data and 'data' in data:
                                data = data['data']
                            
                            await callback(data)
                        except Exception as e:
                            self.logger.error("websocket", f"Error processing message: {e}")
                            
            except (ConnectionClosedError, ConnectionClosedOK) as e:
                self.logger.logger.warning(f"WebSocket closed: {stream_name} - {e}")
            except Exception as e:
                self.logger.error("websocket", f"WebSocket error: {stream_name} - {e}")
            
            # Attempt reconnection with exponential backoff
            if self._ws_running.get(stream_name, False):
                attempts = self._ws_reconnect_attempts.get(stream_name, 0)
                self._ws_reconnect_attempts[stream_name] = attempts + 1
                
                if attempts >= max_reconnect_attempts:
                    self.logger.logger.error(f"Max reconnection attempts reached for {stream_name}")
                    break
                
                # Calculate delay with jitter
                delay = min(base_delay * (2 ** attempts), max_delay)
                jitter = random.uniform(0, 0.1 * delay)
                total_delay = delay + jitter
                
                self.logger.logger.info(
                    f"Reconnecting to {stream_name} in {total_delay:.1f}s (attempt {attempts + 1}/{max_reconnect_attempts})"
                )
                await asyncio.sleep(total_delay)
        
        self.logger.logger.info(f"WebSocket handler stopped: {stream_name}")
    
    async def subscribe_to_trades(self, symbol: str, callback: Callable) -> str:
        """Subscribe to real-time trade stream.
        
        Args:
            symbol: Trading pair symbol
            callback: Async function to call with trade data
            
        Returns:
            Stream ID for managing this subscription
        """
        stream_name = f"{symbol.lower()}@trade"
        self._ws_running[stream_name] = True
        
        async def _trade_callback(data: Dict[str, Any]):
            """Parse trade data and call user callback."""
            trade_data = {
                'symbol': data.get('s'),
                'trade_id': data.get('t'),
                'price': Decimal(data.get('p', 0)),
                'quantity': Decimal(data.get('q', 0)),
                'buyer_order_id': data.get('b'),
                'seller_order_id': data.get('a'),
                'timestamp': data.get('T', 0) / 1000,
                'is_buyer_maker': data.get('m', False),
                'is_best_match': data.get('M', True)
            }
            await callback(trade_data)
        
        # Start WebSocket connection task
        task = asyncio.create_task(
            self._ws_connect_with_retry(stream_name, _trade_callback)
        )
        self._ws_tasks[stream_name] = task
        
        return stream_name
    
    async def subscribe_to_orderbook(self, symbol: str, callback: Callable, depth: int = 100) -> str:
        """Subscribe to real-time orderbook updates.
        
        Args:
            symbol: Trading pair symbol
            callback: Async function to call with orderbook data
            depth: Orderbook depth (5, 10, 20, 50, 100, 500, 1000)
            
        Returns:
            Stream ID for managing this subscription
        """
        # Validate depth
        valid_depths = [5, 10, 20, 50, 100, 500, 1000]
        if depth not in valid_depths:
            depth = min(valid_depths, key=lambda x: abs(x - depth))
        
        stream_name = f"{symbol.lower()}@depth{depth}@100ms"
        self._ws_running[stream_name] = True
        
        async def _orderbook_callback(data: Dict[str, Any]):
            """Parse orderbook data and call user callback."""
            bids = [[Decimal(p), Decimal(q)] for p, q in data.get('b', [])]
            asks = [[Decimal(p), Decimal(q)] for p, q in data.get('a', [])]
            
            orderbook_data = {
                'symbol': data.get('s'),
                'event_time': data.get('E'),
                'first_update_id': data.get('U'),
                'final_update_id': data.get('u'),
                'bids': bids,
                'asks': asks,
                'timestamp': time.time()
            }
            await callback(orderbook_data)
        
        task = asyncio.create_task(
            self._ws_connect_with_retry(stream_name, _orderbook_callback)
        )
        self._ws_tasks[stream_name] = task
        
        return stream_name
    
    async def subscribe_to_klines(self, symbol: str, interval: str, callback: Callable) -> str:
        """Subscribe to real-time kline/candlestick data.
        
        Args:
            symbol: Trading pair symbol
            interval: Kline interval (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M)
            callback: Async function to call with kline data
            
        Returns:
            Stream ID for managing this subscription
        """
        stream_name = f"{symbol.lower()}@kline_{interval}"
        self._ws_running[stream_name] = True
        
        async def _kline_callback(data: Dict[str, Any]):
            """Parse kline data and call user callback."""
            k = data.get('k', {})
            
            kline_data = {
                'symbol': data.get('s'),
                'event_time': data.get('E'),
                'start_time': k.get('t'),
                'close_time': k.get('T'),
                'interval': k.get('i'),
                'first_trade_id': k.get('f'),
                'last_trade_id': k.get('L'),
                'open': Decimal(k.get('o', 0)),
                'close': Decimal(k.get('c', 0)),
                'high': Decimal(k.get('h', 0)),
                'low': Decimal(k.get('l', 0)),
                'volume': Decimal(k.get('v', 0)),
                'quote_volume': Decimal(k.get('q', 0)),
                'trades': k.get('n'),
                'taker_buy_base_volume': Decimal(k.get('V', 0)),
                'taker_buy_quote_volume': Decimal(k.get('Q', 0)),
                'is_closed': k.get('x', False),
                'timestamp': time.time()
            }
            await callback(kline_data)
        
        task = asyncio.create_task(
            self._ws_connect_with_retry(stream_name, _kline_callback)
        )
        self._ws_tasks[stream_name] = task
        
        return stream_name
    
    async def subscribe_to_ticker(self, symbol: str, callback: Callable) -> str:
        """Subscribe to 24hr rolling window ticker statistics.
        
        Args:
            symbol: Trading pair symbol
            callback: Async function to call with ticker data
            
        Returns:
            Stream ID for managing this subscription
        """
        stream_name = f"{symbol.lower()}@ticker"
        self._ws_running[stream_name] = True
        
        async def _ticker_callback(data: Dict[str, Any]):
            """Parse ticker data and call user callback."""
            ticker_data = {
                'symbol': data.get('s'),
                'price_change': Decimal(data.get('p', 0)),
                'price_change_percent': Decimal(data.get('P', 0)),
                'weighted_avg_price': Decimal(data.get('w', 0)),
                'prev_close_price': Decimal(data.get('x', 0)),
                'last_price': Decimal(data.get('c', 0)),
                'bid_price': Decimal(data.get('b', 0)),
                'ask_price': Decimal(data.get('a', 0)),
                'open_price': Decimal(data.get('o', 0)),
                'high_price': Decimal(data.get('h', 0)),
                'low_price': Decimal(data.get('l', 0)),
                'volume': Decimal(data.get('v', 0)),
                'quote_volume': Decimal(data.get('q', 0)),
                'open_time': data.get('O'),
                'close_time': data.get('C'),
                'first_trade_id': data.get('F'),
                'last_trade_id': data.get('L'),
                'trade_count': data.get('n'),
                'timestamp': time.time()
            }
            await callback(ticker_data)
        
        task = asyncio.create_task(
            self._ws_connect_with_retry(stream_name, _ticker_callback)
        )
        self._ws_tasks[stream_name] = task
        
        return stream_name
    
    async def subscribe_combined(self, streams: List[str], callback: Callable) -> str:
        """Subscribe to multiple streams in a single connection.
        
        Args:
            streams: List of stream names (e.g., ['btcusdt@trade', 'ethusdt@trade'])
            callback: Async function to call with data
            
        Returns:
            Combined stream ID
        """
        combined_name = '/'.join(streams)
        self._ws_running[combined_name] = True
        
        task = asyncio.create_task(
            self._ws_connect_with_retry(combined_name, callback, is_combined=True)
        )
        self._ws_tasks[combined_name] = task
        
        return combined_name
    
    async def unsubscribe(self, stream_id: str) -> None:
        """Unsubscribe from a WebSocket stream.
        
        Args:
            stream_id: Stream ID returned from subscribe method
        """
        self._ws_running[stream_id] = False
        
        # Cancel the task
        if stream_id in self._ws_tasks:
            task = self._ws_tasks[stream_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self._ws_tasks[stream_id]
        
        # Close the connection
        if stream_id in self._ws_connections:
            try:
                await self._ws_connections[stream_id].close()
            except Exception:
                pass
            del self._ws_connections[stream_id]
        
        # Clean up other state
        if stream_id in self._ws_reconnect_attempts:
            del self._ws_reconnect_attempts[stream_id]
        
        self.logger.logger.info(f"Unsubscribed from stream: {stream_id}")
    
    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all WebSocket streams."""
        stream_ids = list(self._ws_running.keys())
        for stream_id in stream_ids:
            await self.unsubscribe(stream_id)
        self.logger.logger.info("All WebSocket subscriptions cleared")
    
    async def disconnect(self) -> None:
        """Disconnect from exchange and cleanup WebSocket connections."""
        await self.unsubscribe_all()
        await super().disconnect()
    
    # ==================== Utility Methods ====================
    
    async def ping(self) -> bool:
        """Test connectivity to the REST API.
        
        Returns:
            True if connection is successful
        """
        try:
            await self._make_request("GET", "/api/v3/ping")
            return True
        except Exception:
            return False
    
    async def get_server_time(self) -> int:
        """Get current server time.
        
        Returns:
            Server time in milliseconds
        """
        response = await self._make_request("GET", "/api/v3/time")
        return response.get('serverTime', 0)
    
    async def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange trading rules and symbol information.
        
        Returns:
            Dictionary with exchange info
        """
        return await self._make_request("GET", "/api/v3/exchangeInfo")
    
    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[Dict[str, Any]]:
        """Get recent trades for a symbol.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of trades (max 1000)
            
        Returns:
            List of trade dictionaries
        """
        params = {
            'symbol': symbol.upper(),
            'limit': min(limit, 1000)
        }
        response = await self._make_request("GET", "/api/v3/trades", params=params)
        
        return [
            {
                'id': trade['id'],
                'price': Decimal(trade['price']),
                'qty': Decimal(trade['qty']),
                'quote_qty': Decimal(trade['quoteQty']),
                'time': trade['time'],
                'is_buyer_maker': trade['isBuyerMaker'],
                'is_best_match': trade['isBestMatch']
            }
            for trade in response
        ]
    
    async def test_order(self, order: Order) -> bool:
        """Test an order without actually placing it.
        
        Args:
            order: Order to test
            
        Returns:
            True if order would be valid
        """
        params = {
            'symbol': order.symbol.upper(),
            'side': order.side.value.upper(),
            'type': self._convert_order_type(order.order_type),
            'quantity': str(order.quantity)
        }
        
        if order.order_type in [OrderType.LIMIT, OrderType.STOP_LIMIT]:
            params['timeInForce'] = self._convert_time_in_force(order.time_in_force)
            if order.price:
                params['price'] = str(order.price)
        
        if order.stop_price:
            params['stopPrice'] = str(order.stop_price)
        
        try:
            await self._make_request(
                "POST", "/api/v3/order/test", params=params, signed=True
            )
            return True
        except Exception:
            return False


# For testing
if __name__ == "__main__":
    async def test_adapter():
        """Test the Binance adapter."""
        # Use testnet credentials
        adapter = BinanceAdapter(
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
            
            # Get exchange info
            print("\nGetting exchange info...")
            info = await adapter.get_exchange_info()
            print(f"✓ Exchange: {info.get('timezone')}")
            print(f"✓ Symbols: {len(info.get('symbols', []))}")
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await adapter.disconnect()
    
    # Run test
    asyncio.run(test_adapter())
