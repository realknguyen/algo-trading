"""Coinbase Advanced Trade API v3 adapter implementation.

This adapter uses the Coinbase Developer Platform (CDP) API with JWT authentication.
Supports both REST API v3 and WebSocket feed for real-time data.

References:
- https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api
- https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview
- https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication
"""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from adapters.base_adapter import (
    BaseExchangeAdapter,
    Balance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Ticker,
    TimeInForce,
)


# Optional imports for JWT generation - will gracefully degrade if not available
try:
    import jwt
    from cryptography.hazmat.primitives import serialization

    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False


class CoinbaseAdvancedTradeAdapter(BaseExchangeAdapter):
    """Coinbase Advanced Trade API v3 adapter.

    Features:
    - JWT authentication for Advanced Trade API (CDP keys)
    - REST API v3 endpoints (accounts, orders, products, portfolios)
    - WebSocket subscriptions (market data, user data, orderbook)
    - Pagination handling for list endpoints
    - Sandbox support

    Authentication uses ECDSA (ES256) JWT tokens with CDP API keys.
    The private key must be in PEM format (EC PRIVATE KEY).

    Example:
        adapter = CoinbaseAdvancedTradeAdapter(
            api_key="organizations/{org_id}/apiKeys/{key_id}",
            api_secret="-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n",
            sandbox=True
        )
        async with adapter:
            balances = await adapter.get_balances()
    """

    # REST API endpoints
    REST_SANDBOX_URL = "https://api-public.sandbox.exchange.coinbase.com"
    REST_LIVE_URL = "https://api.coinbase.com"
    REST_API_PATH = "/api/v3/brokerage"

    # WebSocket endpoints
    WS_SANDBOX_URL = "wss://ws-direct.sandbox.exchange.coinbase.com"
    WS_LIVE_URL = "wss://advanced-trade-ws.coinbase.com"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        rate_limit_per_second: float = 10.0,
        portfolio_id: Optional[str] = None,
        **kwargs,
    ):
        """Initialize Coinbase Advanced Trade adapter.

        Args:
            api_key: CDP API key name (format: organizations/{org_id}/apiKeys/{key_id})
            api_secret: CDP API private key in PEM format (EC PRIVATE KEY)
            sandbox: Use sandbox environment
            rate_limit_per_second: Rate limit for API requests
            portfolio_id: Default portfolio UUID for portfolio-specific operations
        """
        self.portfolio_id = portfolio_id
        self._ws_client = None
        self._ws_subscriptions: Dict[str, Set[str]] = {}  # channel -> set of product_ids
        self._ws_callbacks: Dict[str, Callable] = {}  # channel -> callback function
        self._ws_task = None
        self._ws_connected = False

        # Select appropriate URLs
        if sandbox:
            # Sandbox uses the exchange API structure
            base_url = self.REST_SANDBOX_URL
            ws_url = self.WS_SANDBOX_URL
        else:
            base_url = self.REST_LIVE_URL
            ws_url = self.WS_LIVE_URL

        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            ws_url=ws_url,
        )

        if not JWT_AVAILABLE:
            self.logger.logger.warning(
                "JWT libraries not available. Install with: pip install PyJWT cryptography"
            )

    def _build_jwt(self, request_method: str, request_path: str) -> str:
        """Build JWT token for API authentication.

        Uses ES256 (ECDSA with P-256 and SHA-256) as required by Coinbase CDP.

        Args:
            request_method: HTTP method (GET, POST, etc.)
            request_path: Full request path including query string

        Returns:
            JWT token string

        Raises:
            RuntimeError: If JWT libraries are not available
        """
        if not JWT_AVAILABLE:
            raise RuntimeError(
                "JWT authentication requires PyJWT and cryptography. "
                "Install with: pip install PyJWT cryptography"
            )

        # Parse host from base_url
        host = self.base_url.replace("https://", "").replace("http://", "")

        # Build the URI claim
        uri = f"{request_method} {host}{request_path}"

        # Load private key
        private_key_bytes = self.api_secret.encode("utf-8")
        private_key = serialization.load_pem_private_key(private_key_bytes, password=None)

        # Build JWT payload
        now = int(time.time())
        jwt_payload = {
            "sub": self.api_key,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,  # Token expires in 2 minutes
            "uri": uri,
        }

        # Encode JWT with ES256
        jwt_token = jwt.encode(
            jwt_payload,
            private_key,
            algorithm="ES256",
            headers={"kid": self.api_key, "nonce": secrets.token_hex(16)},
        )

        return jwt_token

    def _sign_request(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """Sign request with JWT authentication.

        Args:
            method: HTTP method
            endpoint: API endpoint path
            params: Query parameters or request body

        Returns:
            Headers dict with Authorization header
        """
        # Build full path with query string if GET request
        if method.upper() == "GET" and params:
            query_string = urlencode(sorted(params.items()))
            full_path = f"{endpoint}?{query_string}"
        else:
            full_path = endpoint

        jwt_token = self._build_jwt(method.upper(), full_path)

        return {"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json"}

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """Make HTTP request with rate limiting and retry logic.

        Overrides base method to handle Coinbase-specific error formats.
        """
        try:
            result = await super()._make_request(method, endpoint, params, data, signed, retries)
            return result
        except httpx.HTTPStatusError as e:
            # Handle Coinbase-specific error format
            try:
                error_body = e.response.json()
                if "error" in error_body or "message" in error_body:
                    error_msg = error_body.get("error", error_body.get("message", str(e)))
                    self.logger.error("api_error", f"Coinbase API error: {error_msg}")
            except:
                pass
            raise

    async def _authenticate(self) -> None:
        """Test authentication by getting account info."""
        await self.get_account()

    def _to_coinbase_product_id(self, symbol: str) -> str:
        """Convert standard symbol to Coinbase product ID format.

        Examples:
            BTCUSD -> BTC-USD
            ETHUSDT -> ETH-USDT
        """
        symbol = symbol.upper()

        # If already in correct format, return as-is
        if "-" in symbol:
            return symbol

        # Common quote currencies to check
        quote_currencies = ["USDT", "USD", "EUR", "GBP", "BTC", "ETH", "USDC"]

        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[: -len(quote)]
                return f"{base}-{quote}"

        # Default: try to split at last 3 characters (assumes 3-char quote)
        if len(symbol) > 3:
            return f"{symbol[:-3]}-{symbol[-3:]}"

        return symbol

    def _from_coinbase_product_id(self, product_id: str) -> str:
        """Convert Coinbase product ID to standard symbol format."""
        return product_id.replace("-", "")

    # ==================== REST API v3 Endpoints ====================

    async def get_account(self) -> Dict[str, Any]:
        """Get list of accounts (wallets) for the user.

        Returns:
            Dict containing 'accounts' list with account details
        """
        endpoint = f"{self.REST_API_PATH}/accounts"
        return await self._make_request("GET", endpoint, signed=True)

    async def get_account_by_id(self, account_id: str) -> Dict[str, Any]:
        """Get specific account details.

        Args:
            account_id: UUID of the account

        Returns:
            Account details dict
        """
        endpoint = f"{self.REST_API_PATH}/accounts/{account_id}"
        return await self._make_request("GET", endpoint, signed=True)

    async def get_balances(self) -> List[Balance]:
        """Get account balances across all wallets.

        Returns:
            List of Balance objects with non-zero balances
        """
        response = await self.get_account()
        balances = []

        for account in response.get("accounts", []):
            available = Decimal(account.get("available_balance", {}).get("value", 0))
            hold = Decimal(account.get("hold", {}).get("value", 0))

            if available > 0 or hold > 0:
                currency = account.get("currency", "UNKNOWN")
                balances.append(
                    Balance(asset=currency, free=available, locked=hold, total=available + hold)
                )

        return balances

    async def get_portfolios(self) -> Dict[str, Any]:
        """Get list of portfolios.

        Returns:
            Dict containing 'portfolios' list
        """
        endpoint = f"{self.REST_API_PATH}/portfolios"
        return await self._make_request("GET", endpoint, signed=True)

    async def get_portfolio_breakdown(self, portfolio_id: Optional[str] = None) -> Dict[str, Any]:
        """Get detailed breakdown of a portfolio including positions.

        Args:
            portfolio_id: Portfolio UUID (uses default if not provided)

        Returns:
            Portfolio breakdown with balances and positions
        """
        pid = portfolio_id or self.portfolio_id or ""
        endpoint = f"{self.REST_API_PATH}/portfolios/{pid}"
        return await self._make_request("GET", endpoint, signed=True)

    async def get_positions(self, portfolio_id: Optional[str] = None) -> List[Position]:
        """Get current positions from portfolio breakdown.

        Args:
            portfolio_id: Portfolio UUID (uses default if not provided)

        Returns:
            List of Position objects
        """
        breakdown = await self.get_portfolio_breakdown(portfolio_id)
        positions = []

        # Get spot positions from portfolio breakdown
        spot_positions = breakdown.get("spot_positions", [])

        for pos in spot_positions:
            asset = pos.get("asset", "")
            quantity = Decimal(pos.get("total_balance", {}).get("value", 0))

            if quantity > 0 and asset not in ["USD", "USDC", "USDT"]:
                # Get current price for the asset
                try:
                    ticker = await self.get_ticker(f"{asset}-USD")
                    positions.append(
                        Position(
                            symbol=f"{asset}USD",
                            quantity=quantity,
                            avg_entry_price=ticker.last,  # Simplified - would need cost basis
                            current_price=ticker.last,
                            unrealized_pnl=Decimal("0"),
                        )
                    )
                except Exception as e:
                    self.logger.logger.debug(f"Could not get price for {asset}: {e}")

        return positions

    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC-USD" or "BTCUSD")

        Returns:
            Ticker object with bid, ask, last price, and volume
        """
        product_id = self._to_coinbase_product_id(symbol)

        # Get best bid/ask
        endpoint = f"{self.REST_API_PATH}/best_bid_ask"
        params = {"product_ids": [product_id]}
        response = await self._make_request("GET", endpoint, params=params, signed=True)

        pricebooks = response.get("pricebooks", [])
        if not pricebooks:
            raise Exception(f"No price data available for {symbol}")

        pricebook = pricebooks[0]
        bids = pricebook.get("bids", [])
        asks = pricebook.get("asks", [])

        best_bid = Decimal(bids[0].get("price", 0)) if bids else Decimal("0")
        best_ask = Decimal(asks[0].get("price", 0)) if asks else Decimal("0")

        # Get 24h stats for volume and last price
        stats_endpoint = f"{self.REST_API_PATH}/products/{product_id}/ticker"
        stats = await self._make_request("GET", stats_endpoint, signed=False)

        return Ticker(
            symbol=symbol,
            bid=best_bid,
            ask=best_ask,
            last=Decimal(stats.get("price", 0)),
            volume=Decimal(stats.get("volume", 0)),
            timestamp=time.time(),
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get Level 2 order book for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            limit: Number of price levels to return (max 1000)

        Returns:
            Dict with 'bids', 'asks', and 'sequence' number
        """
        product_id = self._to_coinbase_product_id(symbol)

        endpoint = f"{self.REST_API_PATH}/product_book"
        params = {"product_id": product_id, "limit": min(limit, 1000)}

        response = await self._make_request("GET", endpoint, params=params, signed=False)
        pricebook = response.get("pricebook", {})

        bids = pricebook.get("bids", [])
        asks = pricebook.get("asks", [])

        return {
            "bids": [[Decimal(b.get("price", 0)), Decimal(b.get("size", 0))] for b in bids],
            "asks": [[Decimal(a.get("price", 0)), Decimal(a.get("size", 0))] for a in asks],
            "sequence": pricebook.get("sequence", 0),
            "timestamp": time.time(),
        }

    async def get_market_data(
        self,
        symbol: str,
        granularity: str = "ONE_HOUR",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 350,
    ) -> List[Dict[str, Any]]:
        """Get historical candle data for a product.

        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            granularity: Candle interval (ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE,
                        THIRTY_MINUTE, ONE_HOUR, TWO_HOUR, SIX_HOUR, ONE_DAY)
            start: Start time in RFC3339 format
            end: End time in RFC3339 format
            limit: Maximum candles to return (max 350)

        Returns:
            List of candle dictionaries with open, high, low, close, volume
        """
        product_id = self._to_coinbase_product_id(symbol)

        endpoint = f"{self.REST_API_PATH}/products/{product_id}/candles"
        params = {"granularity": granularity}

        if start:
            params["start"] = start
        if end:
            params["end"] = end

        response = await self._make_request("GET", endpoint, params=params, signed=False)
        candles = response.get("candles", [])

        # Convert to standard format
        formatted_candles = []
        for candle in candles[:limit]:
            formatted_candles.append(
                {
                    "timestamp": candle.get("start"),
                    "start": candle.get("start"),
                    "open": Decimal(candle.get("open", 0)),
                    "high": Decimal(candle.get("high", 0)),
                    "low": Decimal(candle.get("low", 0)),
                    "close": Decimal(candle.get("close", 0)),
                    "volume": Decimal(candle.get("volume", 0)),
                }
            )

        return formatted_candles

    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV data (base adapter interface).

        Args:
            symbol: Trading pair
            interval: Interval string (1m, 5m, 15m, 1h, 6h, 1d)
            start_time: Unix timestamp
            end_time: Unix timestamp
            limit: Maximum number of candles

        Returns:
            List of candle dictionaries
        """
        # Map common interval strings to Coinbase granularity
        granularity_map = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE",
            "1h": "ONE_HOUR",
            "2h": "TWO_HOUR",
            "6h": "SIX_HOUR",
            "1d": "ONE_DAY",
            "ONE_MINUTE": "ONE_MINUTE",
            "FIVE_MINUTE": "FIVE_MINUTE",
            "FIFTEEN_MINUTE": "FIFTEEN_MINUTE",
            "THIRTY_MINUTE": "THIRTY_MINUTE",
            "ONE_HOUR": "ONE_HOUR",
            "TWO_HOUR": "TWO_HOUR",
            "SIX_HOUR": "SIX_HOUR",
            "ONE_DAY": "ONE_DAY",
        }

        granularity = granularity_map.get(interval, "ONE_HOUR")

        # Convert timestamps to RFC3339 format
        from datetime import datetime, timezone

        start = None
        end = None

        if start_time:
            dt = datetime.fromtimestamp(start_time, tz=timezone.utc)
            start = dt.isoformat().replace("+00:00", "Z")
        if end_time:
            dt = datetime.fromtimestamp(end_time, tz=timezone.utc)
            end = dt.isoformat().replace("+00:00", "Z")

        return await self.get_market_data(
            symbol=symbol, granularity=granularity, start=start, end=end, limit=limit
        )

    def _build_order_config(self, order: Order) -> Dict[str, Any]:
        """Build order configuration for Coinbase API.

        Supports:
        - limit_order_gtc: Good till cancelled limit order
        - limit_order_gtd: Good till date limit order
        - market_order: Immediate market order
        - stop_order: Stop loss order

        Args:
            order: Order object with order details

        Returns:
            Order configuration dict for API
        """
        product_id = self._to_coinbase_product_id(order.symbol)

        base_config = {
            "product_id": product_id,
            "side": order.side.value.upper(),
            "client_order_id": order.client_order_id or f"algo-{int(time.time() * 1000)}",
        }

        # Add portfolio ID if specified
        if self.portfolio_id:
            base_config["portfolio_id"] = self.portfolio_id

        order_configs = {}

        if order.order_type == OrderType.MARKET:
            order_configs["market_market_ioc"] = {
                "quote_size": str(order.quantity)  # or base_size depending on side
            }

        elif order.order_type == OrderType.LIMIT:
            if order.time_in_force == TimeInForce.GTC:
                order_configs["limit_limit_gtc"] = {
                    "base_size": str(order.quantity),
                    "limit_price": str(order.price),
                    "post_only": False,
                }
            else:
                # GTD or other TIF
                from datetime import datetime, timedelta, timezone

                end_time = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                order_configs["limit_limit_gtd"] = {
                    "base_size": str(order.quantity),
                    "limit_price": str(order.price),
                    "end_time": end_time,
                    "post_only": False,
                }

        elif order.order_type == OrderType.STOP_LOSS:
            order_configs["stop_limit_stop_limit_gtc"] = {
                "base_size": str(order.quantity),
                "limit_price": str(order.price) if order.price else str(order.stop_price),
                "stop_price": str(order.stop_price),
                "stop_direction": (
                    "STOP_DIRECTION_STOP_DOWN"
                    if order.side == OrderSide.SELL
                    else "STOP_DIRECTION_STOP_UP"
                ),
            }

        base_config["order_configuration"] = order_configs
        return base_config

    async def place_order(self, order: Order) -> Order:
        """Place a new order.

        Args:
            order: Order object with symbol, side, type, quantity, price, etc.

        Returns:
            Updated Order object with order_id and status from exchange
        """
        endpoint = f"{self.REST_API_PATH}/orders"

        order_config = self._build_order_config(order)

        response = await self._make_request("POST", endpoint, data=order_config, signed=True)

        # Update order with response
        success = response.get("success", False)

        if success:
            order.order_id = response.get("order_id")
            order.status = OrderStatus.OPEN

            # Get additional order details
            try:
                order_details = await self.get_order(order.symbol, order.order_id)
                order.status = order_details.status
                order.filled_quantity = order_details.filled_quantity
                order.avg_fill_price = order_details.avg_fill_price
            except Exception as e:
                self.logger.logger.debug(f"Could not fetch order details: {e}")
        else:
            order.status = OrderStatus.REJECTED
            error_message = response.get("error_response", {}).get("message", "Unknown error")
            self.logger.error("place_order", f"Order rejected: {error_message}")

        return order

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing order.

        Args:
            symbol: Trading pair (required for interface, not used by Coinbase)
            order_id: Order ID to cancel

        Returns:
            True if cancellation successful
        """
        endpoint = f"{self.REST_API_PATH}/orders/batch_cancel"

        data = {"order_ids": [order_id]}

        try:
            response = await self._make_request("POST", endpoint, data=data, signed=True)

            # Check results
            results = response.get("results", [])
            if results:
                result = results[0]
                if result.get("success", False):
                    return True
                else:
                    error = result.get("error_response", {}).get("message", "Unknown error")
                    self.logger.error("cancel_order", f"Failed to cancel: {error}")
                    return False
            return True
        except Exception as e:
            self.logger.error("cancel_order", f"Failed to cancel order: {e}")
            return False

    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Get order status and details.

        Args:
            symbol: Trading pair (required for interface, not used by Coinbase)
            order_id: Order ID to query

        Returns:
            Order object with current status
        """
        endpoint = f"{self.REST_API_PATH}/orders/historical/{order_id}"

        response = await self._make_request("GET", endpoint, signed=True)
        order_data = response.get("order", {})

        return self._parse_order_response(order_data)

    def _parse_order_response(self, order_data: Dict[str, Any]) -> Order:
        """Parse Coinbase order response to Order object."""
        product_id = order_data.get("product_id", "")

        # Determine order type from configuration
        config = order_data.get("order_configuration", {})
        if "market_market_ioc" in config:
            order_type = OrderType.MARKET
        elif "limit_limit_gtc" in config or "limit_limit_gtd" in config:
            order_type = OrderType.LIMIT
        elif "stop_limit_stop_limit_gtc" in config or "stop_limit_stop_limit_gtd" in config:
            order_type = OrderType.STOP_LOSS
        else:
            order_type = OrderType.MARKET

        # Get filled quantity
        filled_value = Decimal(order_data.get("filled_value", 0))
        filled_size = Decimal(order_data.get("filled_size", 0))

        avg_fill_price = None
        if filled_size > 0:
            avg_fill_price = filled_value / filled_size

        return Order(
            symbol=self._from_coinbase_product_id(product_id),
            side=OrderSide.BUY if order_data.get("side") == "BUY" else OrderSide.SELL,
            order_type=order_type,
            quantity=Decimal(
                order_data.get("order_configuration", {})
                .get("limit_limit_gtc", {})
                .get("base_size", 0)
                or order_data.get("order_configuration", {})
                .get("market_market_ioc", {})
                .get("quote_size", 0)
                or order_data.get("filled_size", 0)
            ),
            price=Decimal(
                order_data.get("order_configuration", {})
                .get("limit_limit_gtc", {})
                .get("limit_price", 0)
                or order_data.get("order_configuration", {})
                .get("limit_limit_gtd", {})
                .get("limit_price", 0)
                or 0
            )
            or None,
            order_id=order_data.get("order_id"),
            status=self._parse_order_status(order_data.get("status", "UNKNOWN")),
            filled_quantity=filled_size,
            avg_fill_price=avg_fill_price,
            created_at=order_data.get("created_time"),
        )

    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse Coinbase order status to internal OrderStatus."""
        status_map = {
            "PENDING": OrderStatus.PENDING,
            "OPEN": OrderStatus.OPEN,
            "FILLED": OrderStatus.FILLED,
            "CANCELLED": OrderStatus.CANCELLED,
            "EXPIRED": OrderStatus.EXPIRED,
            "FAILED": OrderStatus.REJECTED,
            "UNKNOWN": OrderStatus.PENDING,
        }
        return status_map.get(status, OrderStatus.PENDING)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.

        Args:
            symbol: Optional filter by trading pair

        Returns:
            List of open Order objects
        """
        endpoint = f"{self.REST_API_PATH}/orders/historical/batch"
        params = {"order_status": ["OPEN", "PENDING"]}

        if symbol:
            params["product_id"] = self._to_coinbase_product_id(symbol)

        # Handle pagination
        all_orders = []
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor

            response = await self._make_request("GET", endpoint, params=params, signed=True)
            orders_data = response.get("orders", [])

            for order_data in orders_data:
                all_orders.append(self._parse_order_response(order_data))

            # Check for more pages
            cursor = response.get("cursor")
            has_next = response.get("has_next", False)

            if not has_next or not cursor:
                break

        return all_orders

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get detailed order status (Coinbase-specific method).

        Args:
            order_id: Order ID to query

        Returns:
            Raw order status response from API
        """
        endpoint = f"{self.REST_API_PATH}/orders/historical/{order_id}"
        return await self._make_request("GET", endpoint, signed=True)

    async def preview_order(self, order: Order) -> Dict[str, Any]:
        """Preview an order to see estimated fees and impact.

        Args:
            order: Order to preview

        Returns:
            Preview response with estimated fees
        """
        endpoint = f"{self.REST_API_PATH}/orders/preview"
        order_config = self._build_order_config(order)

        return await self._make_request("POST", endpoint, data=order_config, signed=True)

    # ==================== WebSocket Methods ====================

    def _build_ws_jwt(self) -> str:
        """Build JWT token for WebSocket authentication."""
        if not JWT_AVAILABLE:
            raise RuntimeError("JWT libraries required for WebSocket authentication")

        private_key_bytes = self.api_secret.encode("utf-8")
        private_key = serialization.load_pem_private_key(private_key_bytes, password=None)

        now = int(time.time())
        jwt_payload = {
            "sub": self.api_key,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
        }

        return jwt.encode(
            jwt_payload,
            private_key,
            algorithm="ES256",
            headers={"kid": self.api_key, "nonce": secrets.token_hex(16)},
        )

    async def _ws_connect(self) -> None:
        """Establish WebSocket connection."""
        if self._ws_connected:
            return

        try:
            self._ws_client = await websockets.connect(self.ws_url)
            self._ws_connected = True
            self.logger.logger.info("WebSocket connected")

            # Start message handler task
            self._ws_task = asyncio.create_task(self._ws_message_handler())

        except Exception as e:
            self.logger.error("ws_connect", f"Failed to connect: {e}")
            raise

    async def _ws_message_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for message in self._ws_client:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    channel = data.get("channel", "")

                    # Route to appropriate callback
                    if channel in self._ws_callbacks:
                        callback = self._ws_callbacks[channel]
                        await callback(data)
                    elif msg_type == "error":
                        self.logger.error("ws_message", f"WebSocket error: {data}")
                    elif msg_type == "subscriptions":
                        self.logger.logger.info(f"Subscription update: {data}")

                except Exception as e:
                    self.logger.error("ws_handler", f"Error processing message: {e}")

        except ConnectionClosed:
            self.logger.logger.warning("WebSocket connection closed")
            self._ws_connected = False
        except Exception as e:
            self.logger.error("ws_handler", f"WebSocket error: {e}")
            self._ws_connected = False

    async def _ws_send(self, message: Dict[str, Any]) -> None:
        """Send message over WebSocket with JWT."""
        if not self._ws_connected or not self._ws_client:
            await self._ws_connect()

        # Add JWT for authenticated channels
        jwt_token = self._build_ws_jwt()
        message["jwt"] = jwt_token

        await self._ws_client.send(json.dumps(message))

    async def subscribe_to_heartbeat(
        self, product_ids: List[str], callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to heartbeat channel for connection keepalive.

        The heartbeat channel sends periodic messages to keep connection alive.

        Args:
            product_ids: List of product IDs to subscribe to
            callback: Optional callback function for heartbeat messages
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {"type": "subscribe", "channel": "heartbeat", "product_ids": product_ids}

        if callback:
            self._ws_callbacks["heartbeat"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("heartbeat", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to heartbeat for {product_ids}")

    async def subscribe_to_status(
        self, product_ids: List[str], callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to product status updates.

        Provides updates when products change status (e.g., online/offline).

        Args:
            product_ids: List of product IDs to monitor
            callback: Optional callback for status updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {"type": "subscribe", "channel": "status", "product_ids": product_ids}

        if callback:
            self._ws_callbacks["status"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("status", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to status for {product_ids}")

    async def subscribe_to_ticker(
        self, product_ids: List[str], batch: bool = False, callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to real-time ticker updates.

        Args:
            product_ids: List of product IDs for ticker data
            batch: Use ticker_batch channel for batched updates
            callback: Optional callback for ticker updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        channel = "ticker_batch" if batch else "ticker"

        message = {"type": "subscribe", "channel": channel, "product_ids": product_ids}

        if callback:
            self._ws_callbacks[channel] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault(channel, set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to {channel} for {product_ids}")

    async def subscribe_to_level2(
        self, product_ids: List[str], callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to Level 2 order book updates.

        Provides real-time order book snapshots and updates.

        Args:
            product_ids: List of product IDs for order book data
            callback: Optional callback for order book updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {"type": "subscribe", "channel": "level2", "product_ids": product_ids}

        if callback:
            self._ws_callbacks["level2"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("level2", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to level2 for {product_ids}")

    async def subscribe_to_user(
        self, product_ids: List[str], callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to user-specific order and trade updates.

        Requires authentication. Provides updates on:
        - Order status changes
        - Fills/executions
        - Account balance changes

        Args:
            product_ids: List of product IDs to monitor
            callback: Optional callback for user updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {"type": "subscribe", "channel": "user", "product_ids": product_ids}

        if callback:
            self._ws_callbacks["user"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("user", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to user channel for {product_ids}")

    async def subscribe_to_market_trades(
        self, product_ids: List[str], callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to real-time market trades.

        Args:
            product_ids: List of product IDs for trade data
            callback: Optional callback for trade updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {"type": "subscribe", "channel": "market_trades", "product_ids": product_ids}

        if callback:
            self._ws_callbacks["market_trades"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("market_trades", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to market_trades for {product_ids}")

    async def subscribe_to_candles(
        self,
        product_ids: List[str],
        granularity: str = "ONE_MINUTE",
        callback: Optional[Callable] = None,
    ) -> None:
        """Subscribe to real-time candle updates.

        Args:
            product_ids: List of product IDs for candle data
            granularity: Candle interval (ONE_MINUTE, FIVE_MINUTE, etc.)
            callback: Optional callback for candle updates
        """
        product_ids = [self._to_coinbase_product_id(p) for p in product_ids]

        message = {
            "type": "subscribe",
            "channel": "candles",
            "product_ids": product_ids,
            "granularity": granularity,
        }

        if callback:
            self._ws_callbacks["candles"] = callback

        await self._ws_send(message)
        self._ws_subscriptions.setdefault("candles", set()).update(product_ids)
        self.logger.logger.info(f"Subscribed to candles ({granularity}) for {product_ids}")

    async def unsubscribe(self, channel: str, product_ids: Optional[List[str]] = None) -> None:
        """Unsubscribe from a channel.

        Args:
            channel: Channel name to unsubscribe from
            product_ids: Optional list of specific products to unsubscribe
        """
        message = {"type": "unsubscribe", "channel": channel}

        if product_ids:
            product_ids = [self._to_coinbase_product_id(p) for p in product_ids]
            message["product_ids"] = product_ids

        await self._ws_send(message)

        # Update tracking
        if channel in self._ws_subscriptions:
            if product_ids:
                self._ws_subscriptions[channel].difference_update(product_ids)
            else:
                del self._ws_subscriptions[channel]

        self.logger.logger.info(f"Unsubscribed from {channel}")

    async def disconnect(self) -> None:
        """Disconnect from exchange and close WebSocket."""
        # Close WebSocket
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._ws_client:
            await self._ws_client.close()
            self._ws_client = None
            self._ws_connected = False

        # Close HTTP client
        await super().disconnect()

    # ==================== Utility Methods ====================

    async def list_products(
        self, product_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List available products (trading pairs).

        Args:
            product_type: Filter by type (SPOT, FUTURE)
            limit: Maximum products to return

        Returns:
            List of product details
        """
        endpoint = f"{self.REST_API_PATH}/products"
        params = {"limit": limit}

        if product_type:
            params["product_type"] = product_type

        # Handle pagination
        all_products = []
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor

            response = await self._make_request("GET", endpoint, params=params, signed=False)
            products = response.get("products", [])
            all_products.extend(products)

            cursor = response.get("cursor")
            has_next = response.get("has_next", False)

            if not has_next or not cursor or len(all_products) >= limit:
                break

        return all_products[:limit]

    async def get_product(self, symbol: str) -> Dict[str, Any]:
        """Get detailed information about a specific product.

        Args:
            symbol: Trading pair (e.g., "BTC-USD")

        Returns:
            Product details dict
        """
        product_id = self._to_coinbase_product_id(symbol)
        endpoint = f"{self.REST_API_PATH}/products/{product_id}"
        return await self._make_request("GET", endpoint, signed=False)

    async def get_transaction_summary(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Get transaction summary for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Transaction summary with fees and volumes
        """
        endpoint = f"{self.REST_API_PATH}/transaction_summary"
        params = {"start_date": start_date, "end_date": end_date}
        return await self._make_request("GET", endpoint, params=params, signed=True)


# Alias for backward compatibility
CoinbaseAdapter = CoinbaseAdvancedTradeAdapter
