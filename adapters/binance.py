"""Binance exchange adapter implementation."""

import asyncio
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Optional, Dict, Any, List

import websockets
from websockets.exceptions import ConnectionClosed

from adapters.base_adapter import (
    BaseExchangeAdapter, Order, OrderType, OrderSide, 
    OrderStatus, TimeInForce, Ticker, Position, Balance
)


class BinanceAdapter(BaseExchangeAdapter):
    """Binance exchange adapter.
    
    Supports both spot and futures trading.
    Uses Testnet by default for safety.
    """
    
    # API endpoints
    SPOT_BASE_URL = "https://api.binance.com"
    SPOT_TESTNET_URL = "https://testnet.binance.vision"
    FUTURES_BASE_URL = "https://fapi.binance.com"
    FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"
    
    WS_SPOT_URL = "wss://stream.binance.com:9443/ws"
    WS_TESTNET_URL = "wss://testnet.binance.vision/ws"
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        futures: bool = False,
        rate_limit_per_second: float = 10.0
    ):
        self.futures = futures
        
        # Select appropriate base URL
        if futures:
            base_url = self.FUTURES_TESTNET_URL if sandbox else self.FUTURES_BASE_URL
        else:
            base_url = self.SPOT_TESTNET_URL if sandbox else self.SPOT_BASE_URL
        
        ws_url = self.WS_TESTNET_URL if sandbox else self.WS_SPOT_URL
        
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            ws_url=ws_url
        )
        
        self.recv_window = 5000  # 5 seconds
    
    def _sign_request(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """Sign request with HMAC SHA256."""
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        params['recvWindow'] = self.recv_window
        
        # Create query string
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        
        # Create signature
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        params['signature'] = signature
        
        return {
            'X-MBX-APIKEY': self.api_key
        }
    
    async def _authenticate(self) -> None:
        """Test authentication by getting account info."""
        await self.get_account()
    
    async def get_account(self) -> Dict[str, Any]:
        """Get account information."""
        params = {}
        response = await self._make_request("GET", "/api/v3/account", params=params, signed=True)
        return response
    
    async def get_balances(self) -> List[Balance]:
        """Get account balances."""
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
        """Get current ticker for symbol."""
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
    
    async def get_orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get order book for symbol."""
        params = {
            'symbol': symbol.upper(),
            'limit': min(limit, 1000)
        }
        response = await self._make_request("GET", "/api/v3/depth", params=params)
        
        return {
            'bids': [[Decimal(p), Decimal(q)] for p, q in response['bids']],
            'asks': [[Decimal(p), Decimal(q)] for p, q in response['asks']],
            'timestamp': response.get('lastUpdateId', int(time.time() * 1000))
        }
    
    def _convert_order_type(self, order_type: OrderType) -> str:
        """Convert internal order type to Binance format."""
        mapping = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP_LOSS: "STOP_LOSS",
            OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
            OrderType.TAKE_PROFIT: "TAKE_PROFIT"
        }
        return mapping.get(order_type, "MARKET")
    
    def _convert_time_in_force(self, tif: TimeInForce) -> str:
        """Convert internal TIF to Binance format."""
        mapping = {
            TimeInForce.GTC: "GTC",
            TimeInForce.IOC: "IOC",
            TimeInForce.FOK: "FOK"
        }
        return mapping.get(tif, "GTC")
    
    async def place_order(self, order: Order) -> Order:
        """Place a new order."""
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
        
        if order.client_order_id:
            params['newClientOrderId'] = order.client_order_id
        
        response = await self._make_request(
            "POST", "/api/v3/order", params=params, signed=True
        )
        
        # Update order with response data
        order.order_id = str(response['orderId'])
        order.status = self._parse_order_status(response['status'])
        order.filled_quantity = Decimal(response.get('executedQty', 0))
        
        if response.get('avgPrice'):
            order.avg_fill_price = Decimal(response['avgPrice'])
        
        return order
    
    def _parse_order_status(self, status: str) -> OrderStatus:
        """Parse Binance order status to internal format."""
        mapping = {
            'NEW': OrderStatus.OPEN,
            'PARTIALLY_FILLED': OrderStatus.PARTIALLY_FILLED,
            'FILLED': OrderStatus.FILLED,
            'CANCELED': OrderStatus.CANCELLED,
            'PENDING_CANCEL': OrderStatus.PENDING,
            'REJECTED': OrderStatus.REJECTED,
            'EXPIRED': OrderStatus.EXPIRED
        }
        return mapping.get(status, OrderStatus.PENDING)
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an existing order."""
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        
        try:
            await self._make_request("DELETE", "/api/v3/order", params=params, signed=True)
            return True
        except Exception as e:
            self.logger.error("cancel_order", f"Failed to cancel order: {e}")
            return False
    
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """Get order status."""
        params = {
            'symbol': symbol.upper(),
            'orderId': order_id
        }
        
        response = await self._make_request("GET", "/api/v3/order", params=params, signed=True)
        
        return Order(
            symbol=response['symbol'],
            side=OrderSide.BUY if response['side'] == 'BUY' else OrderSide.SELL,
            order_type=self._parse_order_type(response['type']),
            quantity=Decimal(response['origQty']),
            price=Decimal(response.get('price', 0)) if response.get('price') else None,
            stop_price=Decimal(response.get('stopPrice', 0)) if response.get('stopPrice') else None,
            order_id=str(response['orderId']),
            status=self._parse_order_status(response['status']),
            filled_quantity=Decimal(response['executedQty']),
            avg_fill_price=Decimal(response.get('avgPrice', 0)) if response.get('avgPrice') else None
        )
    
    def _parse_order_type(self, order_type: str) -> OrderType:
        """Parse Binance order type to internal format."""
        mapping = {
            'MARKET': OrderType.MARKET,
            'LIMIT': OrderType.LIMIT,
            'STOP_LOSS': OrderType.STOP_LOSS,
            'STOP_LOSS_LIMIT': OrderType.STOP_LIMIT,
            'TAKE_PROFIT': OrderType.TAKE_PROFIT,
            'TAKE_PROFIT_LIMIT': OrderType.TAKE_PROFIT
        }
        return mapping.get(order_type, OrderType.MARKET)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        
        response = await self._make_request("GET", "/api/v3/openOrders", params=params, signed=True)
        
        orders = []
        for data in response:
            orders.append(await self.get_order(data['symbol'], str(data['orderId'])))
        
        return orders
    
    async def get_positions(self) -> List[Position]:
        """Get current positions (for Binance, this is non-zero balances)."""
        balances = await self.get_balances()
        positions = []
        
        # For spot trading, positions are non-zero balances
        # This is simplified - in reality you'd track based on trades
        for balance in balances:
            if balance.asset != 'USDT' and balance.total > 0:
                try:
                    ticker = await self.get_ticker(f"{balance.asset}USDT")
                    positions.append(Position(
                        symbol=f"{balance.asset}USDT",
                        quantity=balance.free,
                        avg_entry_price=ticker.last,  # Simplified
                        current_price=ticker.last,
                        unrealized_pnl=Decimal("0")  # Would need cost basis tracking
                    ))
                except Exception:
                    pass
        
        return positions
    
    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get historical OHLCV data.
        
        Intervals: 1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
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
                'open': Decimal(candle[1]),
                'high': Decimal(candle[2]),
                'low': Decimal(candle[3]),
                'close': Decimal(candle[4]),
                'volume': Decimal(candle[5]),
                'close_time': candle[6],
                'quote_volume': Decimal(candle[7])
            })
        
        return candles
    
    async def subscribe_ticker(self, symbol: str, callback) -> None:
        """Subscribe to real-time ticker updates via WebSocket."""
        stream = f"{symbol.lower()}@ticker"
        ws_url = f"{self.ws_url}/{stream}"
        
        try:
            async with websockets.connect(ws_url) as ws:
                self.logger.logger.info(f"Subscribed to {symbol} ticker")
                
                async for message in ws:
                    try:
                        data = json.loads(message)
                        ticker = Ticker(
                            symbol=data['s'],
                            bid=Decimal(data['b']),
                            ask=Decimal(data['a']),
                            last=Decimal(data['c']),
                            volume=Decimal(data['v']),
                            timestamp=data['E'] / 1000
                        )
                        await callback(ticker)
                    except Exception as e:
                        self.logger.error("websocket", f"Error processing message: {e}")
                        
        except ConnectionClosed:
            self.logger.logger.warning("WebSocket connection closed")
        except Exception as e:
            self.logger.error("websocket", f"WebSocket error: {e}")
