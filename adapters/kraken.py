"""Kraken exchange adapter implementation."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from decimal import Decimal
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from adapters.base_adapter import (
    BaseExchangeAdapter, Order, OrderType, OrderSide, 
    OrderStatus, TimeInForce, Ticker, Position, Balance,
    ExchangeError, AuthenticationError, InsufficientFundsError, InvalidSymbolError
)


class KrakenRateLimitTier(Enum):
    """Kraken API rate limit tiers."""
    TIER_1 = "Tier 1"  # 15 calls per 3 seconds
    TIER_2 = "Tier 2"  # 20 calls per 3 seconds  
    TIER_3 = "Tier 3"  # 30 calls per 3 seconds
    TIER_4 = "Tier 4"  # 60 calls per 3 seconds


@dataclass
class KrakenRateLimit:
    """Kraken rate limit tracking."""
    tier: KrakenRateLimitTier = KrakenRateLimitTier.TIER_1
    calls_per_3s: int = 15
    remaining_calls: int = 15
    last_reset: float = field(default_factory=time.time)
    
    def decrement(self) -> None:
        """Decrement remaining calls."""
        now = time.time()
        if now - self.last_reset >= 3:
            self.remaining_calls = self.calls_per_3s
            self.last_reset = now
        self.remaining_calls = max(0, self.remaining_calls - 1)
    
    def should_wait(self) -> bool:
        """Check if we should wait before making a call."""
        now = time.time()
        if now - self.last_reset >= 3:
            return False
        return self.remaining_calls <= 0
    
    def wait_time(self) -> float:
        """Get time to wait before next call."""
        now = time.time()
        elapsed = now - self.last_reset
        return max(0, 3 - elapsed)


class KrakenAdapter(BaseExchangeAdapter):
    """Kraken exchange adapter with full REST and WebSocket support.
    
    Features:
    - REST API for trading operations
    - WebSocket for real-time data feeds
    - Rate limit tracking with tier-based configuration
    - OTP support for withdrawal-enabled keys
    - Auto-reconnect logic for WebSocket
    
    Reference: https://docs.kraken.com/rest/
    """
    
    # REST API endpoints
    BASE_URL = "https://api.kraken.com"
    BASE_URL_SANDBOX = "https://api.sandbox.kraken.com"  # Paper trading
    
    # WebSocket endpoints
    WS_PUBLIC_URL = "wss://ws.kraken.com"
    WS_PRIVATE_URL = "wss://ws-auth.kraken.com"
    
    # Rate limit configuration per tier
    RATE_LIMITS = {
        KrakenRateLimitTier.TIER_1: 15,  # 15 calls per 3 seconds
        KrakenRateLimitTier.TIER_2: 20,  # 20 calls per 3 seconds
        KrakenRateLimitTier.TIER_3: 30,  # 30 calls per 3 seconds
        KrakenRateLimitTier.TIER_4: 60,  # 60 calls per 3 seconds
    }
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        rate_limit_tier: KrakenRateLimitTier = KrakenRateLimitTier.TIER_1,
        otp: Optional[str] = None,
        ws_reconnect_interval: float = 5.0,
        ws_max_reconnects: int = 10
    ):
        # Determine base URL based on sandbox mode
        base_url = self.BASE_URL_SANDBOX if sandbox else self.BASE_URL
        
        # Calculate rate limit per second
        calls_per_3s = self.RATE_LIMITS[rate_limit_tier]
        rate_limit_per_second = calls_per_3s / 3.0
        
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            ws_url=self.WS_PUBLIC_URL
        )
        
        # Kraken-specific configuration
        self.otp = otp  # Two-factor authentication code
        self.rate_limit = KrakenRateLimit(
            tier=rate_limit_tier,
            calls_per_3s=calls_per_3s,
            remaining_calls=calls_per_3s
        )
        
        # WebSocket configuration
        self.ws_reconnect_interval = ws_reconnect_interval
        self.ws_max_reconnects = ws_max_reconnects
        self._ws_reconnect_count = 0
        
        # WebSocket clients
        self.ws_public: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_private: Optional[websockets.WebSocketClientProtocol] = None
        
        # WebSocket subscriptions
        self._ws_subscriptions: Dict[str, Dict[str, Any]] = {}
        self._ws_callbacks: Dict[str, Callable] = {}
        self._ws_running = False
        self._ws_tasks: List[asyncio.Task] = []
        
        # WebSocket token for private connections
        self._ws_token: Optional[str] = None
    
    def _sign_request(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """Generate Kraken API-Sign header.
        
        Kraken authentication uses:
        - API-Key header with the API key
        - API-Sign header with HMAC-SHA512 signature
        
        Signature is computed as:
        HMAC-SHA512 of (URI path + SHA256(nonce + POST data)) using 
        base64 decoded secret key.
        
        Args:
            method: HTTP method (POST for private endpoints)
            endpoint: API endpoint path (e.g., "/0/private/Balance")
            params: Request parameters including nonce
            
        Returns:
            Dictionary with API-Key and API-Sign headers
        """
        # Ensure nonce is present
        if 'nonce' not in params:
            params['nonce'] = int(time.time() * 1000)
        
        # Add OTP if configured (for withdrawal-enabled keys)
        if self.otp and 'otp' not in params:
            params['otp'] = self.otp
        
        # Form-encode the POST data
        post_data = urllib.parse.urlencode(params)
        
        # Create the message to sign: endpoint + SHA256(nonce + post_data)
        nonce_str = str(params['nonce'])
        encoded = (nonce_str + post_data).encode('utf-8')
        sha256_hash = hashlib.sha256(encoded).digest()
        message = endpoint.encode('utf-8') + sha256_hash
        
        # Generate HMAC-SHA512 signature using base64-decoded secret
        try:
            secret_bytes = base64.b64decode(self.api_secret)
        except Exception as e:
            raise AuthenticationError(f"Invalid API secret format: {e}")
        
        signature = hmac.new(
            secret_bytes,
            message,
            hashlib.sha512
        ).digest()
        
        return {
            'API-Key': self.api_key,
            'API-Sign': base64.b64encode(signature).decode()
        }
    
    def _sign_challenge(self, challenge: str) -> str:
        """Sign WebSocket authentication challenge.
        
        Args:
            challenge: WebSocket challenge string
            
        Returns:
            Base64 encoded signature
        """
        # Sign the challenge with HMAC-SHA512
        secret_bytes = base64.b64decode(self.api_secret)
        signature = hmac.new(
            secret_bytes,
            challenge.encode('utf-8'),
            hashlib.sha512
        ).digest()
        return base64.b64encode(signature).decode()
    
    async def _rate_limited_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make rate-limited request with decrement tracking.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional arguments for _make_request
            
        Returns:
            JSON response
        """
        # Wait if rate limit exceeded
        while self.rate_limit.should_wait():
            wait_time = self.rate_limit.wait_time()
            self.logger.logger.warning(f"Rate limit reached, waiting {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)
        
        # Decrement rate limit counter
        self.rate_limit.decrement()
        
        # Make the request
        return await self._make_request(method, endpoint, **kwargs)
    
    async def _authenticate(self) -> None:
        """Test authentication by getting account balance."""
        try:
            await self.get_balances()
        except Exception as e:
            raise AuthenticationError(f"Failed to authenticate: {e}")
    
    async def _make_private_request(
        self, 
        endpoint: str, 
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to private API with rate limiting.
        
        Args:
            endpoint: Private API endpoint (e.g., "/0/private/Balance")
            params: Request parameters
            
        Returns:
            API result data
            
        Raises:
            ExchangeError: On API error
            AuthenticationError: On auth failure
            InsufficientFundsError: On balance issues
        """
        params = params or {}
        
        # Add nonce
        params['nonce'] = int(time.time() * 1000)
        
        # Sign request
        headers = self._sign_request("POST", endpoint, params)
        
        # Make rate-limited request
        response = await self._rate_limited_request(
            "POST", 
            endpoint, 
            data=params, 
            signed=True
        )
        
        # Handle Kraken error format
        if response.get('error'):
            errors = response['error']
            error_str = ', '.join(errors) if isinstance(errors, list) else str(errors)
            
            # Map specific errors to exceptions
            if any('Invalid key' in e or 'Invalid signature' in e for e in errors):
                raise AuthenticationError(f"Authentication failed: {error_str}")
            elif any('Insufficient funds' in e or 'Insufficient margin' in e for e in errors):
                raise InsufficientFundsError(f"Insufficient funds: {error_str}")
            elif any('Unknown asset pair' in e or 'Invalid asset' in e for e in errors):
                raise InvalidSymbolError(f"Invalid symbol: {error_str}")
            else:
                raise ExchangeError(f"Kraken API error: {error_str}")
        
        return response.get('result', {})
    
    async def _make_public_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make public API request with rate limiting.
        
        Args:
            endpoint: Public API endpoint (e.g., "/0/public/Ticker")
            params: Query parameters
            
        Returns:
            API result data
        """
        # Public endpoints use GET
        response = await self._rate_limited_request(
            "GET",
            endpoint,
            params=params
        )
        
        if response.get('error'):
            errors = response['error']
            error_str = ', '.join(errors) if isinstance(errors, list) else str(errors)
            raise ExchangeError(f"Kraken API error: {error_str}")
        
        return response.get('result', {})
    
    async def get_account(self) -> Dict[str, Any]:
        """Get account information and balances.
        
        Returns:
            Dictionary containing account balance data
        """
        return await self._make_private_request("/0/private/Balance")
    
    async def get_balances(self) -> List[Balance]:
        """Get account balances with extended information.
        
        Uses BalanceEx for more detailed balance info including
        free/locked separation.
        
        Returns:
            List of Balance objects
        """
        # Use BalanceEx for more detailed info
        try:
            result = await self._make_private_request("/0/private/BalanceEx")
        except Exception:
            # Fallback to simple Balance if BalanceEx not available
            result = await self._make_private_request("/0/private/Balance")
        
        balances = []
        
        for asset, data in result.items():
            # Kraken uses X/Z prefixes for some assets (XBT, ZUSD, etc.)
            clean_asset = self._normalize_asset(asset)
            
            if isinstance(data, dict):
                # BalanceEx format
                free = Decimal(data.get('balance', '0'))
                hold = Decimal(data.get('hold_trade', '0'))
                total = free + hold
            else:
                # Simple Balance format
                total = Decimal(data)
                free = total
                hold = Decimal("0")
            
            if total > 0:
                balances.append(Balance(
                    asset=clean_asset,
                    free=free,
                    locked=hold,
                    total=total
                ))
        
        return balances
    
    def _normalize_asset(self, asset: str) -> str:
        """Normalize Kraken asset naming to standard format.
        
        Kraken uses prefixes:
        - X prefix for crypto (XBT, XETH)
        - Z prefix for fiat (ZUSD, ZEUR)
        
        Args:
            asset: Kraken asset code
            
        Returns:
            Normalized asset code
        """
        # Remove X/Z prefixes
        if asset.startswith('X') and len(asset) > 1:
            # XBT -> BTC, XETH -> ETH
            return asset[1:]
        elif asset.startswith('Z') and len(asset) > 1:
            # ZUSD -> USD, ZEUR -> EUR
            return asset[1:]
        return asset
    
    async def get_market_data(self, symbol: str) -> Ticker:
        """Get current market data (ticker) for symbol.
        
        This is an alias for get_ticker() for API consistency.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            
        Returns:
            Ticker object with market data
        """
        return await self.get_ticker(symbol)
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            
        Returns:
            Ticker object with bid, ask, last price, and volume
        """
        kraken_symbol = self._to_kraken_symbol(symbol)
        
        params = {'pair': kraken_symbol}
        result = await self._make_public_request("/0/public/Ticker", params=params)
        
        # Extract ticker data (result keys vary by pair)
        ticker_data = list(result.values())[0]
        
        # Kraken ticker format:
        # a = ask array [price, whole lot volume, lot volume]
        # b = bid array [price, whole lot volume, lot volume]
        # c = last trade [price, lot volume]
        # v = volume [today, last 24h]
        return Ticker(
            symbol=symbol,
            bid=Decimal(ticker_data['b'][0]),
            ask=Decimal(ticker_data['a'][0]),
            last=Decimal(ticker_data['c'][0]),
            volume=Decimal(ticker_data['v'][1]),  # 24h volume
            timestamp=time.time()
        )
    
    def _to_kraken_symbol(self, symbol: str) -> str:
        """Convert standard symbol to Kraken format.
        
        Kraken uses specific naming conventions:
        - Crypto: XBT (not BTC), XETH, etc.
        - Fiat: USD, EUR (with Z prefix in old format)
        - Pairs: XBTUSD, ETHUSD, etc.
        
        Args:
            symbol: Standard symbol like "BTCUSD"
            
        Returns:
            Kraken symbol like "XXBTZUSD" or "XBTUSD"
        """
        symbol = symbol.upper().replace('-', '').replace('/', '')
        
        # Common mappings to Kraken's WS REST API format
        mappings = {
            'BTCUSD': 'XXBTZUSD',
            'BTCUSDT': 'XBTUSDT',
            'BTCUSDC': 'XBTUSDC',
            'ETHUSD': 'XETHZUSD',
            'ETHUSDT': 'ETHUSDT',
            'ETHBTC': 'XETHXXBT',
            'LTCUSD': 'XLTCZUSD',
            'LTCBTC': 'XLTCXXBT',
            'XRPUSD': 'XXRPZUSD',
            'XRPBTC': 'XXRPXXBT',
            'ADAUSD': 'ADAUSD',
            'ADAUSDT': 'ADAUSDT',
            'SOLUSD': 'SOLUSD',
            'DOTUSD': 'DOTUSD',
            'LINKUSD': 'LINKUSD',
            'MATICUSD': 'MATICUSD',
            'UNIUSD': 'UNIUSD',
            'AAVEUSD': 'AAVEUSD',
            'SNXUSD': 'SNXUSD',
            'CRVUSD': 'CRVUSD',
            'COMPUSD': 'COMPUSD',
            'YFIUSD': 'YFIUSD',
            'MKRUSD': 'MKRUSD',
            'EURUSD': 'EURUSD',
            'GBPUSD': 'GBPUSD',
        }
        
        return mappings.get(symbol, symbol)
    
    def _from_kraken_symbol(self, kraken_symbol: str) -> str:
        """Convert Kraken symbol to standard format.
        
        Args:
            kraken_symbol: Kraken symbol like "XXBTZUSD"
            
        Returns:
            Standard symbol like "BTCUSD"
        """
        mappings = {
            'XXBTZUSD': 'BTCUSD',
            'XBTUSDT': 'BTCUSDT',
            'XBTUSDC': 'BTCUSDC',
            'XETHZUSD': 'ETHUSD',
            'ETHUSDT': 'ETHUSDT',
            'XETHXXBT': 'ETHBTC',
            'XLTCZUSD': 'LTCUSD',
            'XLTCXXBT': 'LTCBTC',
            'XXRPZUSD': 'XRPUSD',
            'XXRPXXBT': 'XRPBTC',
        }
        
        return mappings.get(kraken_symbol, kraken_symbol)
    
    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get order book depth for symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            limit: Number of orders to return (max 500)
            
        Returns:
            Dictionary with bids, asks, and timestamp
        """
        kraken_symbol = self._to_kraken_symbol(symbol)
        
        params = {
            'pair': kraken_symbol,
            'count': min(limit, 500)
        }
        
        result = await self._make_public_request("/0/public/Depth", params=params)
        
        # Extract orderbook data
        orderbook = list(result.values())[0]
        
        return {
            'symbol': symbol,
            'bids': [[Decimal(p), Decimal(q)] for p, q, _ in orderbook['bids']],
            'asks': [[Decimal(p), Decimal(q)] for p, q, _ in orderbook['asks']],
            'timestamp': time.time()
        }
    
    async def get_recent_trades(self, symbol: str, since: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get recent trades for a symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            since: Return trades since timestamp (optional)
            limit: Maximum number of trades to return
            
        Returns:
            List of trade dictionaries
        """
        kraken_symbol = self._to_kraken_symbol(symbol)
        
        params = {
            'pair': kraken_symbol
        }
        if since:
            params['since'] = since
        
        result = await self._make_public_request("/0/public/Trades", params=params)
        
        # Extract trades
        trades_data = list(result.values())[0]
        
        trades = []
        for trade in trades_data[:limit]:
            # Trade format: [price, volume, time, side, order_type, misc]
            trades.append({
                'price': Decimal(trade[0]),
                'volume': Decimal(trade[1]),
                'timestamp': float(trade[2]),
                'side': trade[3],  # b = buy, s = sell
                'order_type': trade[4],  # m = market, l = limit
                'misc': trade[5] if len(trade) > 5 else ''
            })
        
        return trades
    
    def _convert_order_type(self, order_type: OrderType) -> str:
        """Convert internal order type to Kraken format.
        
        Args:
            order_type: Internal OrderType enum
            
        Returns:
            Kraken order type string
        """
        mapping = {
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
            OrderType.STOP_LOSS: "stop-loss",
            OrderType.TAKE_PROFIT: "take-profit",
            OrderType.STOP_LIMIT: "stop-loss-limit"
        }
        return mapping.get(order_type, "market")
    
    def _convert_time_in_force(self, tif: TimeInForce) -> Optional[str]:
        """Convert internal TimeInForce to Kraken format.
        
        Args:
            tif: TimeInForce enum
            
        Returns:
            Kraken time in force string or None
        """
        mapping = {
            TimeInForce.GTC: "GTC",
            TimeInForce.IOC: "IOC",
            TimeInForce.FOK: "FOK"
        }
        return mapping.get(tif)
    
    async def place_order(self, order: Order) -> Order:
        """Place a new order on Kraken.
        
        Supports market, limit, stop-loss, take-profit orders.
        
        Args:
            order: Order object with order details
            
        Returns:
            Updated Order object with order_id and status
            
        Raises:
            InsufficientFundsError: If not enough balance
            InvalidSymbolError: If symbol not found
        """
        kraken_symbol = self._to_kraken_symbol(order.symbol)
        
        params = {
            'pair': kraken_symbol,
            'type': order.side.value,
            'ordertype': self._convert_order_type(order.order_type),
            'volume': str(order.quantity)
        }
        
        # Add price for limit orders
        if order.price:
            params['price'] = str(order.price)
        
        # Add stop price for stop orders
        if order.stop_price:
            params['price2'] = str(order.stop_price)
        
        # Add client order ID (userref must be integer for Kraken)
        if order.client_order_id:
            try:
                params['userref'] = int(order.client_order_id)
            except ValueError:
                # Generate numeric ID from string
                params['userref'] = abs(hash(order.client_order_id)) % (10 ** 9)
        
        # Add time in force
        tif = self._convert_time_in_force(order.time_in_force)
        if tif:
            params['timeinforce'] = tif
        
        # Add validate flag for test orders (optional, for dry-run)
        if self.sandbox:
            params['validate'] = 'true'
        
        result = await self._make_private_request("/0/private/AddOrder", params)
        
        # Update order with response
        txid = result.get('txid', [None])[0]
        order.order_id = txid
        order.status = OrderStatus.OPEN
        order.created_at = time.time()
        
        self.logger.logger.info(f"Placed order {txid} for {order.symbol}")
        
        return order
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing order.
        
        Args:
            symbol: Trading pair (not used by Kraken, but kept for interface)
            order_id: Order transaction ID to cancel
            
        Returns:
            True if cancelled successfully, False otherwise
        """
        params = {'txid': order_id}
        
        try:
            await self._make_private_request("/0/private/CancelOrder", params)
            self.logger.logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            self.logger.error("cancel_order", f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def get_order_status(self, symbol: str, order_id: str) -> Order:
        """Get order status by order_id.
        
        This is an alias for get_order() for API consistency.
        
        Args:
            symbol: Trading pair
            order_id: Order transaction ID
            
        Returns:
            Order object with current status
        """
        return await self.get_order(symbol, order_id)
    
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Get order details and status.
        
        Args:
            symbol: Trading pair
            order_id: Order transaction ID
            
        Returns:
            Order object with full details
            
        Raises:
            ExchangeError: If order not found
        """
        params = {'txid': order_id}
        
        result = await self._make_private_request("/0/private/QueryOrders", params)
        order_data = result.get(order_id, {})
        
        if not order_data:
            raise ExchangeError(f"Order {order_id} not found")
        
        descr = order_data.get('descr', {})
        
        return Order(
            symbol=self._from_kraken_symbol(descr.get('pair', symbol)),
            side=OrderSide.BUY if descr.get('type') == 'buy' else OrderSide.SELL,
            order_type=self._parse_order_type(descr.get('ordertype', 'market')),
            quantity=Decimal(order_data.get('vol', 0)),
            price=Decimal(descr.get('price', 0)) if descr.get('price') else None,
            stop_price=Decimal(descr.get('price2', 0)) if descr.get('price2') else None,
            order_id=order_id,
            status=self._parse_order_status(order_data.get('status', 'open')),
            filled_quantity=Decimal(order_data.get('vol_exec', 0)),
            avg_fill_price=Decimal(order_data.get('price', 0)) if order_data.get('price') else None,
            created_at=float(order_data.get('opentm', time.time()))
        )
    
    def _parse_order_type(self, order_type: str) -> OrderType:
        """Parse Kraken order type to internal format.
        
        Args:
            order_type: Kraken order type string
            
        Returns:
            OrderType enum value
        """
        mapping = {
            'market': OrderType.MARKET,
            'limit': OrderType.LIMIT,
            'stop-loss': OrderType.STOP_LOSS,
            'take-profit': OrderType.TAKE_PROFIT,
            'stop-loss-limit': OrderType.STOP_LIMIT,
            'take-profit-limit': OrderType.TAKE_PROFIT,
            'trailing-stop': OrderType.TRAILING_STOP
        }
        return mapping.get(order_type, OrderType.MARKET)
    
    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse Kraken order status to internal format.
        
        Args:
            status: Kraken order status string
            
        Returns:
            OrderStatus enum value
        """
        mapping = {
            'pending': OrderStatus.PENDING,
            'open': OrderStatus.OPEN,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELLED,
            'expired': OrderStatus.EXPIRED
        }
        return mapping.get(status, OrderStatus.PENDING)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of open Order objects
        """
        result = await self._make_private_request("/0/private/OpenOrders")
        open_orders = result.get('open', {})
        
        orders = []
        for order_id, order_data in open_orders.items():
            try:
                order = await self.get_order(symbol or "", order_id)
                # Filter by symbol if specified
                if symbol is None or order.symbol.upper() == symbol.upper():
                    orders.append(order)
            except Exception as e:
                self.logger.logger.warning(f"Failed to get order {order_id}: {e}")
        
        return orders
    
    async def get_positions(self) -> List[Position]:
        """Get current open positions.
        
        For margin trading, returns open margin positions.
        For spot trading, consider using get_balances().
        
        Returns:
            List of Position objects
        """
        try:
            result = await self._make_private_request("/0/private/OpenPositions")
            positions = []
            
            for pos_id, pos_data in result.items():
                # Calculate PnL
                cost = Decimal(pos_data.get('cost', 0))
                vol = Decimal(pos_data.get('vol', 0))
                current_price = Decimal(pos_data.get('price', 0))
                
                positions.append(Position(
                    symbol=self._from_kraken_symbol(pos_data.get('pair', '')),
                    quantity=vol,
                    avg_entry_price=Decimal(pos_data.get('cost', 0)) / vol if vol > 0 else Decimal("0"),
                    current_price=current_price,
                    unrealized_pnl=Decimal(pos_data.get('net', 0)),
                    realized_pnl=Decimal("0")  # Not provided by this endpoint
                ))
            
            return positions
        except Exception as e:
            self.logger.error("get_positions", f"Failed to get positions: {e}")
            return []
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 720
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV data.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSD")
            interval: Candle interval (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M)
            start_time: Start timestamp (optional)
            end_time: End timestamp (optional) - not used by Kraken API
            limit: Maximum candles to return (max 720)
            
        Returns:
            List of candle dictionaries
            
        Note:
            Kraken intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 minutes
        """
        kraken_symbol = self._to_kraken_symbol(symbol)
        
        # Convert interval to minutes
        interval_mapping = {
            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '4h': 240, '1d': 1440, '1w': 10080, '1M': 21600
        }
        interval_minutes = interval_mapping.get(interval, 60)
        
        params = {
            'pair': kraken_symbol,
            'interval': interval_minutes
        }
        
        if start_time:
            params['since'] = start_time
        
        result = await self._make_public_request("/0/public/OHLC", params=params)
        
        # Extract OHLC data
        pair_name = [k for k in result.keys() if k != 'last'][0]
        ohlc_data = result[pair_name]
        
        candles = []
        for candle in ohlc_data[:limit]:
            # Format: [time, open, high, low, close, vwap, volume, count]
            candles.append({
                'timestamp': int(candle[0]),
                'open': Decimal(candle[1]),
                'high': Decimal(candle[2]),
                'low': Decimal(candle[3]),
                'close': Decimal(candle[4]),
                'vwap': Decimal(candle[5]),
                'volume': Decimal(candle[6]),
                'count': int(candle[7])
            })
        
        return candles
    
    # ==================== WebSocket Implementation ====================
    
    async def connect_websocket(self, private: bool = False) -> None:
        """Establish WebSocket connection.
        
        Args:
            private: Connect to private WebSocket for account data
        """
        if private:
            # Get WebSocket token first
            if not self._ws_token:
                await self._refresh_ws_token()
            
            self.ws_private = await self._connect_ws(self.WS_PRIVATE_URL, private=True)
            self._ws_tasks.append(asyncio.create_task(self._ws_loop(self.ws_private, private=True)))
        else:
            self.ws_public = await self._connect_ws(self.WS_PUBLIC_URL, private=False)
            self._ws_tasks.append(asyncio.create_task(self._ws_loop(self.ws_public, private=False)))
        
        self._ws_running = True
    
    async def _connect_ws(self, url: str, private: bool = False) -> websockets.WebSocketClientProtocol:
        """Connect to WebSocket with retry logic.
        
        Args:
            url: WebSocket URL
            private: Whether this is a private connection
            
        Returns:
            WebSocket client protocol
        """
        while self._ws_reconnect_count < self.ws_max_reconnects:
            try:
                ws = await websockets.connect(url)
                
                if private and self._ws_token:
                    # Authenticate private connection
                    await self._ws_authenticate(ws)
                
                self._ws_reconnect_count = 0
                self.logger.logger.info(f"WebSocket connected: {url}")
                return ws
                
            except (ConnectionClosed, InvalidStatusCode, OSError) as e:
                self._ws_reconnect_count += 1
                wait_time = min(self.ws_reconnect_interval * (2 ** self._ws_reconnect_count), 60)
                self.logger.logger.warning(
                    f"WebSocket connection failed ({self._ws_reconnect_count}/{self.ws_max_reconnects}), "
                    f"retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
        
        raise ExchangeError(f"Failed to connect WebSocket after {self.ws_max_reconnects} attempts")
    
    async def _ws_authenticate(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Authenticate private WebSocket connection.
        
        Args:
            ws: WebSocket connection
        """
        # Generate challenge response
        challenge_payload = {
            'event': 'challenge',
            'api_key': self.api_key
        }
        
        await ws.send(json.dumps(challenge_payload))
        
        # Wait for challenge
        response = await ws.recv()
        data = json.loads(response)
        
        if data.get('event') == 'challenge':
            challenge = data.get('message', '')
            signed_challenge = self._sign_challenge(challenge)
            
            # Send subscription with signed challenge
            auth_payload = {
                'event': 'subscribe',
                'subscription': {
                    'name': 'ownTrades',
                    'token': self._ws_token
                }
            }
            await ws.send(json.dumps(auth_payload))
    
    async def _refresh_ws_token(self) -> None:
        """Refresh WebSocket authentication token."""
        try:
            result = await self._make_private_request("/0/private/GetWebSocketsToken")
            self._ws_token = result.get('token')
            self.logger.logger.info("WebSocket token refreshed")
        except Exception as e:
            self.logger.error("ws_token", f"Failed to get WebSocket token: {e}")
            raise
    
    async def _ws_loop(self, ws: websockets.WebSocketClientProtocol, private: bool = False) -> None:
        """Main WebSocket message loop with auto-reconnect.
        
        Args:
            ws: WebSocket connection
            private: Whether this is a private connection
        """
        ws_type = "private" if private else "public"
        
        while self._ws_running:
            try:
                async for message in ws:
                    await self._handle_ws_message(message, private)
                    
            except ConnectionClosed:
                self.logger.logger.warning(f"WebSocket {ws_type} connection closed, reconnecting...")
                await self._reconnect_ws(private)
            except Exception as e:
                self.logger.error(f"ws_{ws_type}", f"WebSocket error: {e}")
                await asyncio.sleep(self.ws_reconnect_interval)
    
    async def _reconnect_ws(self, private: bool = False) -> None:
        """Reconnect WebSocket and restore subscriptions.
        
        Args:
            private: Whether to reconnect private WebSocket
        """
        try:
            if private:
                if self.ws_private:
                    await self.ws_private.close()
                self.ws_private = await self._connect_ws(self.WS_PRIVATE_URL, private=True)
                # Restore private subscriptions
                for sub_id, sub_info in self._ws_subscriptions.items():
                    if sub_info.get('private'):
                        await self._resubscribe(sub_id, sub_info)
            else:
                if self.ws_public:
                    await self.ws_public.close()
                self.ws_public = await self._connect_ws(self.WS_PUBLIC_URL, private=False)
                # Restore public subscriptions
                for sub_id, sub_info in self._ws_subscriptions.items():
                    if not sub_info.get('private'):
                        await self._resubscribe(sub_id, sub_info)
                        
        except Exception as e:
            self.logger.error("ws_reconnect", f"Failed to reconnect WebSocket: {e}")
    
    async def _resubscribe(self, sub_id: str, sub_info: Dict[str, Any]) -> None:
        """Resubscribe to a channel after reconnection.
        
        Args:
            sub_id: Subscription ID
            sub_info: Subscription information
        """
        ws = self.ws_private if sub_info.get('private') else self.ws_public
        if ws:
            await ws.send(json.dumps(sub_info['payload']))
            self.logger.logger.info(f"Resubscribed to {sub_id}")
    
    async def _handle_ws_message(self, message: str, private: bool = False) -> None:
        """Handle incoming WebSocket message.
        
        Args:
            message: Raw WebSocket message
            private: Whether from private connection
        """
        try:
            data = json.loads(message)
            
            # Handle system messages
            if isinstance(data, dict):
                event = data.get('event')
                
                if event == 'heartbeat':
                    return  # Ignore heartbeats
                    
                elif event == 'systemStatus':
                    status = data.get('status')
                    self.logger.logger.info(f"WebSocket system status: {status}")
                    return
                    
                elif event == 'subscriptionStatus':
                    channel = data.get('channelName', data.get('subscription', {}).get('name'))
                    status = data.get('status')
                    self.logger.logger.info(f"Subscription {channel}: {status}")
                    return
            
            # Handle data arrays [channelID, data, channelName, pair]
            if isinstance(data, list) and len(data) >= 2:
                channel_id = data[0]
                payload = data[1]
                
                # Get channel name and pair if available
                if len(data) >= 4:
                    channel_name = data[2]
                    pair = data[3]
                else:
                    channel_name = self._ws_subscriptions.get(str(channel_id), {}).get('name', 'unknown')
                    pair = ''
                
                # Find and call the appropriate callback
                callback_key = f"{channel_name}:{pair}"
                callback = self._ws_callbacks.get(callback_key) or self._ws_callbacks.get(channel_name)
                
                if callback:
                    try:
                        await callback(payload, pair, channel_name)
                    except Exception as e:
                        self.logger.error("ws_callback", f"Callback error: {e}")
                        
        except json.JSONDecodeError:
            self.logger.logger.warning(f"Invalid WebSocket message: {message}")
        except Exception as e:
            self.logger.error("ws_handler", f"Message handling error: {e}")
    
    async def subscribe_to_ticker(self, symbol: str, callback: Callable[[Dict, str, str], None]) -> None:
        """Subscribe to real-time ticker updates via WebSocket.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            callback: Async function to call with (data, pair, channel_name)
        """
        await self._ensure_public_ws()
        
        # Kraken WebSocket uses slash format for pairs
        ws_symbol = symbol.replace('-', '/').upper()
        
        subscription = {
            'event': 'subscribe',
            'pair': [ws_symbol],
            'subscription': {'name': 'ticker'}
        }
        
        sub_id = f"ticker:{symbol}"
        self._ws_subscriptions[sub_id] = {
            'name': 'ticker',
            'payload': subscription,
            'private': False
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_public.send(json.dumps(subscription))
        self.logger.logger.info(f"Subscribed to ticker: {symbol}")
    
    async def subscribe_to_orderbook(self, symbol: str, callback: Callable[[Dict, str, str], None], depth: int = 10) -> None:
        """Subscribe to real-time orderbook updates via WebSocket.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            callback: Async function to call with (data, pair, channel_name)
            depth: Orderbook depth (10, 25, 100, 500, 1000)
        """
        await self._ensure_public_ws()
        
        ws_symbol = symbol.replace('-', '/').upper()
        
        subscription = {
            'event': 'subscribe',
            'pair': [ws_symbol],
            'subscription': {
                'name': 'book',
                'depth': depth
            }
        }
        
        sub_id = f"book:{symbol}"
        self._ws_subscriptions[sub_id] = {
            'name': 'book',
            'payload': subscription,
            'private': False
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_public.send(json.dumps(subscription))
        self.logger.logger.info(f"Subscribed to orderbook: {symbol}")
    
    async def subscribe_to_trades(self, symbol: str, callback: Callable[[Dict, str, str], None]) -> None:
        """Subscribe to real-time trade updates via WebSocket.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            callback: Async function to call with (data, pair, channel_name)
        """
        await self._ensure_public_ws()
        
        ws_symbol = symbol.replace('-', '/').upper()
        
        subscription = {
            'event': 'subscribe',
            'pair': [ws_symbol],
            'subscription': {'name': 'trade'}
        }
        
        sub_id = f"trade:{symbol}"
        self._ws_subscriptions[sub_id] = {
            'name': 'trade',
            'payload': subscription,
            'private': False
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_public.send(json.dumps(subscription))
        self.logger.logger.info(f"Subscribed to trades: {symbol}")
    
    async def subscribe_to_ohlc(self, symbol: str, callback: Callable[[Dict, str, str], None], interval: int = 1) -> None:
        """Subscribe to OHLC (candlestick) updates via WebSocket.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            callback: Async function to call with (data, pair, channel_name)
            interval: Candle interval in minutes (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)
        """
        await self._ensure_public_ws()
        
        ws_symbol = symbol.replace('-', '/').upper()
        
        subscription = {
            'event': 'subscribe',
            'pair': [ws_symbol],
            'subscription': {
                'name': 'ohlc',
                'interval': interval
            }
        }
        
        sub_id = f"ohlc:{symbol}:{interval}"
        self._ws_subscriptions[sub_id] = {
            'name': 'ohlc',
            'payload': subscription,
            'private': False
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_public.send(json.dumps(subscription))
        self.logger.logger.info(f"Subscribed to OHLC: {symbol} ({interval}m)")
    
    async def subscribe_to_own_trades(self, callback: Callable[[Dict, str, str], None]) -> None:
        """Subscribe to own trade updates via private WebSocket.
        
        Requires authentication.
        
        Args:
            callback: Async function to call with (data, pair, channel_name)
        """
        await self._ensure_private_ws()
        
        subscription = {
            'event': 'subscribe',
            'subscription': {
                'name': 'ownTrades',
                'token': self._ws_token
            }
        }
        
        sub_id = "ownTrades"
        self._ws_subscriptions[sub_id] = {
            'name': 'ownTrades',
            'payload': subscription,
            'private': True
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_private.send(json.dumps(subscription))
        self.logger.logger.info("Subscribed to own trades")
    
    async def subscribe_to_open_orders(self, callback: Callable[[Dict, str, str], None]) -> None:
        """Subscribe to open order updates via private WebSocket.
        
        Requires authentication.
        
        Args:
            callback: Async function to call with (data, pair, channel_name)
        """
        await self._ensure_private_ws()
        
        subscription = {
            'event': 'subscribe',
            'subscription': {
                'name': 'openOrders',
                'token': self._ws_token
            }
        }
        
        sub_id = "openOrders"
        self._ws_subscriptions[sub_id] = {
            'name': 'openOrders',
            'payload': subscription,
            'private': True
        }
        self._ws_callbacks[sub_id] = callback
        
        await self.ws_private.send(json.dumps(subscription))
        self.logger.logger.info("Subscribed to open orders")
    
    async def _ensure_public_ws(self) -> None:
        """Ensure public WebSocket connection is established."""
        if not self.ws_public or self.ws_public.closed:
            await self.connect_websocket(private=False)
    
    async def _ensure_private_ws(self) -> None:
        """Ensure private WebSocket connection is established."""
        if not self.ws_private or self.ws_private.closed:
            await self.connect_websocket(private=True)
    
    async def unsubscribe(self, channel: str, symbol: Optional[str] = None) -> None:
        """Unsubscribe from a WebSocket channel.
        
        Args:
            channel: Channel name (ticker, book, trade, ohlc, etc.)
            symbol: Trading pair (optional, for public channels)
        """
        sub_id = f"{channel}:{symbol}" if symbol else channel
        sub_info = self._ws_subscriptions.get(sub_id)
        
        if not sub_info:
            self.logger.logger.warning(f"Not subscribed to {sub_id}")
            return
        
        ws = self.ws_private if sub_info.get('private') else self.ws_public
        
        if ws and not ws.closed:
            payload = sub_info['payload'].copy()
            payload['event'] = 'unsubscribe'
            await ws.send(json.dumps(payload))
        
        del self._ws_subscriptions[sub_id]
        if sub_id in self._ws_callbacks:
            del self._ws_callbacks[sub_id]
        
        self.logger.logger.info(f"Unsubscribed from {sub_id}")
    
    async def disconnect_websocket(self) -> None:
        """Disconnect all WebSocket connections."""
        self._ws_running = False
        
        # Cancel all WebSocket tasks
        for task in self._ws_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ws_tasks.clear()
        
        # Close connections
        if self.ws_public:
            await self.ws_public.close()
            self.ws_public = None
        
        if self.ws_private:
            await self.ws_private.close()
            self.ws_private = None
        
        self._ws_subscriptions.clear()
        self._ws_callbacks.clear()
        
        self.logger.logger.info("WebSocket connections closed")
    
    async def disconnect(self) -> None:
        """Disconnect from exchange (HTTP and WebSocket)."""
        await self.disconnect_websocket()
        await super().disconnect()
    
    # ==================== Utility Methods ====================
    
    def get_rate_limit_status(self) -> Dict[str, Any]:
        """Get current rate limit status.
        
        Returns:
            Dictionary with rate limit information
        """
        return {
            'tier': self.rate_limit.tier.value,
            'calls_per_3s': self.rate_limit.calls_per_3s,
            'remaining_calls': self.rate_limit.remaining_calls,
            'last_reset': self.rate_limit.last_reset,
            'should_wait': self.rate_limit.should_wait(),
            'wait_time': self.rate_limit.wait_time()
        }
    
    def set_rate_limit_tier(self, tier: KrakenRateLimitTier) -> None:
        """Set the rate limit tier.
        
        Args:
            tier: New rate limit tier
        """
        self.rate_limit.tier = tier
        self.rate_limit.calls_per_3s = self.RATE_LIMITS[tier]
        self.rate_limit.remaining_calls = self.RATE_LIMITS[tier]
        
        # Update the AsyncLimiter as well
        from aiolimiter import AsyncLimiter
        new_rate = self.RATE_LIMITS[tier] / 3.0
        self.rate_limiter = AsyncLimiter(new_rate, time_period=1.0)
        
        self.logger.logger.info(f"Rate limit tier set to {tier.value}")
    
    async def get_server_time(self) -> Dict[str, Any]:
        """Get Kraken server time.
        
        Returns:
            Dictionary with unixtime and rfc1123 time
        """
        return await self._make_public_request("/0/public/Time")
    
    async def get_asset_pairs(self) -> Dict[str, Any]:
        """Get available asset pairs.
        
        Returns:
            Dictionary of asset pair information
        """
        return await self._make_public_request("/0/public/AssetPairs")
    
    async def get_assets(self) -> Dict[str, Any]:
        """Get available assets.
        
        Returns:
            Dictionary of asset information
        """
        return await self._make_public_request("/0/public/Assets")
