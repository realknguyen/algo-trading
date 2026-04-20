"""Data normalization layer for unified exchange data formats."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime
import time


class TradeSide(str, Enum):
    """Unified trade side."""

    BUY = "buy"
    SELL = "sell"


class ExchangeName(str, Enum):
    """Supported exchange names."""

    BINANCE = "binance"
    COINBASE = "coinbase"
    KRAKEN = "kraken"
    ALPACA = "alpaca"
    INTERACTIVE_BROKERS = "interactive_brokers"


@dataclass
class Trade:
    """Unified Trade dataclass.

    Attributes:
        timestamp: Unix timestamp in seconds
        price: Trade price
        quantity: Trade quantity
        side: Trade side (buy/sell)
        exchange: Exchange name
        symbol: Trading symbol
        trade_id: Optional exchange trade ID
        maker_order_id: Optional maker order ID
        taker_order_id: Optional taker order ID
    """

    timestamp: float
    price: Decimal
    quantity: Decimal
    side: TradeSide
    exchange: ExchangeName
    symbol: str
    trade_id: Optional[str] = None
    maker_order_id: Optional[str] = None
    taker_order_id: Optional[str] = None

    def __post_init__(self):
        """Ensure proper types after initialization."""
        if isinstance(self.price, (int, float, str)):
            self.price = Decimal(str(self.price))
        if isinstance(self.quantity, (int, float, str)):
            self.quantity = Decimal(str(self.quantity))
        if isinstance(self.side, str):
            self.side = TradeSide(self.side.lower())
        if isinstance(self.exchange, str):
            self.exchange = ExchangeName(self.exchange.lower())

    @property
    def value(self) -> Decimal:
        """Calculate trade value (price * quantity)."""
        return self.price * self.quantity

    @property
    def datetime(self) -> datetime:
        """Get datetime object from timestamp."""
        return datetime.fromtimestamp(self.timestamp)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "price": str(self.price),
            "quantity": str(self.quantity),
            "side": self.side.value,
            "exchange": self.exchange.value,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "maker_order_id": self.maker_order_id,
            "taker_order_id": self.taker_order_id,
        }


@dataclass
class OrderBookLevel:
    """Single level in order book (price and quantity)."""

    price: Decimal
    quantity: Decimal

    def __post_init__(self):
        if isinstance(self.price, (int, float, str)):
            self.price = Decimal(str(self.price))
        if isinstance(self.quantity, (int, float, str)):
            self.quantity = Decimal(str(self.quantity))

    @property
    def value(self) -> Decimal:
        """Total value at this level."""
        return self.price * self.quantity


@dataclass
class OrderBook:
    """Unified OrderBook dataclass.

    Attributes:
        bids: List of [price, quantity] tuples (sorted descending by price)
        asks: List of [price, quantity] tuples (sorted ascending by price)
        timestamp: Unix timestamp in seconds
        symbol: Trading symbol
        exchange: Exchange name
        sequence: Optional sequence number for ordering
    """

    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    timestamp: float
    symbol: str
    exchange: ExchangeName
    sequence: Optional[int] = None

    def __post_init__(self):
        """Ensure proper types after initialization."""
        if isinstance(self.exchange, str):
            self.exchange = ExchangeName(self.exchange.lower())

        # Convert bid/ask lists to OrderBookLevel if needed
        if self.bids and isinstance(self.bids[0], (list, tuple)):
            self.bids = [OrderBookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in self.bids]
        if self.asks and isinstance(self.asks[0], (list, tuple)):
            self.asks = [OrderBookLevel(Decimal(str(p)), Decimal(str(q))) for p, q in self.asks]

    @property
    def best_bid(self) -> Optional[OrderBookLevel]:
        """Get best (highest) bid."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderBookLevel]:
        """Get best (lowest) ask."""
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> Optional[Decimal]:
        """Calculate bid-ask spread."""
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None

    @property
    def spread_pct(self) -> Optional[Decimal]:
        """Calculate spread as percentage of mid price."""
        if self.best_bid and self.best_ask:
            mid = (self.best_bid.price + self.best_ask.price) / 2
            if mid > 0:
                return (self.spread / mid) * 100
        return None

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Calculate mid price."""
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    def get_bid_depth(self, depth: int = 10) -> Decimal:
        """Calculate total quantity at top N bid levels."""
        return sum(level.quantity for level in self.bids[:depth])

    def get_ask_depth(self, depth: int = 10) -> Decimal:
        """Calculate total quantity at top N ask levels."""
        return sum(level.quantity for level in self.asks[:depth])

    def get_bid_value(self, depth: int = 10) -> Decimal:
        """Calculate total value at top N bid levels."""
        return sum(level.value for level in self.bids[:depth])

    def get_ask_value(self, depth: int = 10) -> Decimal:
        """Calculate total value at top N ask levels."""
        return sum(level.value for level in self.asks[:depth])

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "bids": [{"price": str(l.price), "quantity": str(l.quantity)} for l in self.bids],
            "asks": [{"price": str(l.price), "quantity": str(l.quantity)} for l in self.asks],
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "exchange": self.exchange.value,
            "sequence": self.sequence,
            "spread": str(self.spread) if self.spread else None,
            "mid_price": str(self.mid_price) if self.mid_price else None,
        }


@dataclass
class Ticker:
    """Unified Ticker dataclass.

    Attributes:
        symbol: Trading symbol
        bid: Best bid price
        ask: Best ask price
        last: Last trade price
        volume: 24h volume
        change_pct: 24h price change percentage
        timestamp: Unix timestamp in seconds
        exchange: Exchange name
        high_24h: 24h high price (optional)
        low_24h: 24h low price (optional)
        quote_volume: 24h quote volume (optional)
    """

    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    change_pct: Decimal
    timestamp: float
    exchange: ExchangeName
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None
    quote_volume: Optional[Decimal] = None

    def __post_init__(self):
        """Ensure proper types after initialization."""
        for attr in [
            "bid",
            "ask",
            "last",
            "volume",
            "change_pct",
            "high_24h",
            "low_24h",
            "quote_volume",
        ]:
            value = getattr(self, attr)
            if value is not None and isinstance(value, (int, float, str)):
                setattr(self, attr, Decimal(str(value)))
        if isinstance(self.exchange, str):
            self.exchange = ExchangeName(self.exchange.lower())

    @property
    def spread(self) -> Decimal:
        """Calculate bid-ask spread."""
        return self.ask - self.bid

    @property
    def mid_price(self) -> Decimal:
        """Calculate mid price."""
        return (self.bid + self.ask) / 2

    @property
    def price_change(self) -> Optional[Decimal]:
        """Calculate absolute price change."""
        if self.change_pct is not None:
            # Approximate based on last price
            return (self.change_pct / 100) * self.last
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "symbol": self.symbol,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "last": str(self.last),
            "volume": str(self.volume),
            "change_pct": str(self.change_pct),
            "timestamp": self.timestamp,
            "exchange": self.exchange.value,
            "spread": str(self.spread),
            "mid_price": str(self.mid_price),
        }
        if self.high_24h:
            result["high_24h"] = str(self.high_24h)
        if self.low_24h:
            result["low_24h"] = str(self.low_24h)
        if self.quote_volume:
            result["quote_volume"] = str(self.quote_volume)
        return result


@dataclass
class Candle:
    """Unified Candle (OHLCV) dataclass.

    Attributes:
        open: Opening price
        high: Highest price
        low: Lowest price
        close: Closing price
        volume: Trading volume
        timestamp: Unix timestamp in seconds (candle start time)
        symbol: Trading symbol
        exchange: Exchange name
        interval: Candle interval (e.g., '1m', '1h', '1d')
        quote_volume: Optional quote volume
        trades_count: Optional number of trades
        taker_buy_volume: Optional taker buy volume
    """

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: float
    symbol: str
    exchange: ExchangeName
    interval: str
    quote_volume: Optional[Decimal] = None
    trades_count: Optional[int] = None
    taker_buy_volume: Optional[Decimal] = None

    def __post_init__(self):
        """Ensure proper types after initialization."""
        for attr in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume"]:
            value = getattr(self, attr)
            if value is not None and isinstance(value, (int, float, str)):
                setattr(self, attr, Decimal(str(value)))
        if isinstance(self.exchange, str):
            self.exchange = ExchangeName(self.exchange.lower())

    @property
    def range(self) -> Decimal:
        """Calculate price range (high - low)."""
        return self.high - self.low

    @property
    def change(self) -> Decimal:
        """Calculate price change (close - open)."""
        return self.close - self.open

    @property
    def change_pct(self) -> Decimal:
        """Calculate price change percentage."""
        if self.open != 0:
            return (self.change / self.open) * 100
        return Decimal("0")

    @property
    def body(self) -> Decimal:
        """Calculate candle body size (abs of change)."""
        return abs(self.change)

    @property
    def upper_wick(self) -> Decimal:
        """Calculate upper wick size."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> Decimal:
        """Calculate lower wick size."""
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        """Check if candle is bullish (close > open)."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Check if candle is bearish (close < open)."""
        return self.close < self.open

    @property
    def is_doji(self, threshold: Decimal = Decimal("0.001")) -> bool:
        """Check if candle is a doji (open ~= close)."""
        return abs(self.change) <= threshold * self.open

    @property
    def vwap(self) -> Decimal:
        """Estimate VWAP using OHLC average."""
        return (self.open + self.high + self.low + self.close) / 4

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "exchange": self.exchange.value,
            "interval": self.interval,
            "change_pct": str(self.change_pct),
            "is_bullish": self.is_bullish,
        }
        if self.quote_volume:
            result["quote_volume"] = str(self.quote_volume)
        if self.trades_count:
            result["trades_count"] = self.trades_count
        if self.taker_buy_volume:
            result["taker_buy_volume"] = str(self.taker_buy_volume)
        return result


# ============================================================================
# Exchange-specific conversion functions
# ============================================================================


def normalize_binance_trade(trade_data: Dict[str, Any], symbol: str) -> Trade:
    """Convert Binance trade format to unified Trade.

    Args:
        trade_data: Raw trade data from Binance API
        symbol: Trading symbol

    Returns:
        Unified Trade object
    """
    return Trade(
        timestamp=trade_data.get("T", trade_data.get("time", int(time.time() * 1000))) / 1000,
        price=Decimal(trade_data["p"]),
        quantity=Decimal(trade_data["q"]),
        side=TradeSide.SELL if trade_data.get("m", False) else TradeSide.BUY,
        exchange=ExchangeName.BINANCE,
        symbol=symbol.upper(),
        trade_id=str(trade_data.get("t", trade_data.get("a", ""))),
        maker_order_id=str(trade_data.get("M", "")),
        taker_order_id=str(trade_data.get("t", "")),
    )


def normalize_binance_orderbook(orderbook_data: Dict[str, Any], symbol: str) -> OrderBook:
    """Convert Binance orderbook format to unified OrderBook.

    Args:
        orderbook_data: Raw orderbook data from Binance API
        symbol: Trading symbol

    Returns:
        Unified OrderBook object
    """
    return OrderBook(
        bids=[[Decimal(p), Decimal(q)] for p, q in orderbook_data.get("bids", [])],
        asks=[[Decimal(p), Decimal(q)] for p, q in orderbook_data.get("asks", [])],
        timestamp=time.time(),
        symbol=symbol.upper(),
        exchange=ExchangeName.BINANCE,
        sequence=orderbook_data.get("lastUpdateId"),
    )


def normalize_binance_ticker(ticker_data: Dict[str, Any], symbol: str) -> Ticker:
    """Convert Binance ticker format to unified Ticker.

    Args:
        ticker_data: Raw ticker data from Binance API
        symbol: Trading symbol

    Returns:
        Unified Ticker object
    """
    return Ticker(
        symbol=symbol.upper(),
        bid=Decimal(ticker_data.get("bidPrice", ticker_data.get("b", 0))),
        ask=Decimal(ticker_data.get("askPrice", ticker_data.get("a", 0))),
        last=Decimal(ticker_data.get("lastPrice", ticker_data.get("c", 0))),
        volume=Decimal(ticker_data.get("volume", ticker_data.get("v", 0))),
        change_pct=Decimal(ticker_data.get("priceChangePercent", ticker_data.get("P", 0))),
        timestamp=ticker_data.get("closeTime", int(time.time() * 1000)) / 1000,
        exchange=ExchangeName.BINANCE,
        high_24h=Decimal(ticker_data.get("highPrice", ticker_data.get("h", 0))) or None,
        low_24h=Decimal(ticker_data.get("lowPrice", ticker_data.get("l", 0))) or None,
        quote_volume=Decimal(ticker_data.get("quoteVolume", ticker_data.get("q", 0))) or None,
    )


def normalize_binance_candle(candle_data: List[Any], symbol: str, interval: str) -> Candle:
    """Convert Binance candle format to unified Candle.

    Args:
        candle_data: Raw candle data from Binance API [timestamp, open, high, low, close, volume, ...]
        symbol: Trading symbol
        interval: Candle interval

    Returns:
        Unified Candle object
    """
    return Candle(
        open=Decimal(candle_data[1]),
        high=Decimal(candle_data[2]),
        low=Decimal(candle_data[3]),
        close=Decimal(candle_data[4]),
        volume=Decimal(candle_data[5]),
        timestamp=candle_data[0] / 1000,
        symbol=symbol.upper(),
        exchange=ExchangeName.BINANCE,
        interval=interval,
        quote_volume=Decimal(candle_data[7]) if len(candle_data) > 7 else None,
        trades_count=int(candle_data[8]) if len(candle_data) > 8 else None,
        taker_buy_volume=Decimal(candle_data[9]) if len(candle_data) > 9 else None,
    )


def normalize_coinbase_trade(trade_data: Dict[str, Any], symbol: str) -> Trade:
    """Convert Coinbase trade format to unified Trade.

    Args:
        trade_data: Raw trade data from Coinbase API
        symbol: Trading symbol

    Returns:
        Unified Trade object
    """
    side = TradeSide.BUY if trade_data.get("side") == "buy" else TradeSide.SELL

    return Trade(
        timestamp=trade_data.get("time", time.time()),
        price=Decimal(trade_data["price"]),
        quantity=Decimal(trade_data["size"]),
        side=side,
        exchange=ExchangeName.COINBASE,
        symbol=symbol.upper().replace("-", ""),
        trade_id=trade_data.get("trade_id", trade_data.get("tradeId", "")),
        maker_order_id=trade_data.get("maker_order_id"),
        taker_order_id=trade_data.get("taker_order_id"),
    )


def normalize_coinbase_orderbook(orderbook_data: Dict[str, Any], symbol: str) -> OrderBook:
    """Convert Coinbase orderbook format to unified OrderBook.

    Args:
        orderbook_data: Raw orderbook data from Coinbase API
        symbol: Trading symbol

    Returns:
        Unified OrderBook object
    """
    # Coinbase returns [price, size, num_orders]
    bids_raw = orderbook_data.get("bids", [])
    asks_raw = orderbook_data.get("asks", [])

    return OrderBook(
        bids=[[Decimal(p), Decimal(s)] for p, s, _ in bids_raw],
        asks=[[Decimal(p), Decimal(s)] for p, s, _ in asks_raw],
        timestamp=time.time(),
        symbol=symbol.upper().replace("-", ""),
        exchange=ExchangeName.COINBASE,
        sequence=orderbook_data.get("sequence"),
    )


def normalize_coinbase_ticker(ticker_data: Dict[str, Any], symbol: str) -> Ticker:
    """Convert Coinbase ticker format to unified Ticker.

    Args:
        ticker_data: Raw ticker data from Coinbase API
        symbol: Trading symbol

    Returns:
        Unified Ticker object
    """
    return Ticker(
        symbol=symbol.upper().replace("-", ""),
        bid=Decimal(ticker_data.get("bid", 0)),
        ask=Decimal(ticker_data.get("ask", 0)),
        last=Decimal(ticker_data.get("price", ticker_data.get("last", 0))),
        volume=Decimal(ticker_data.get("volume", 0)),
        change_pct=Decimal("0"),  # Coinbase ticker doesn't include change_pct
        timestamp=time.time(),
        exchange=ExchangeName.COINBASE,
    )


def normalize_coinbase_candle(candle_data: List[Any], symbol: str, interval: str) -> Candle:
    """Convert Coinbase candle format to unified Candle.

    Args:
        candle_data: Raw candle data from Coinbase API [timestamp, low, high, open, close, volume]
        symbol: Trading symbol
        interval: Candle interval

    Returns:
        Unified Candle object
    """
    # Coinbase returns: [timestamp, low, high, open, close, volume]
    return Candle(
        open=Decimal(candle_data[3]),
        high=Decimal(candle_data[2]),
        low=Decimal(candle_data[1]),
        close=Decimal(candle_data[4]),
        volume=Decimal(candle_data[5]),
        timestamp=candle_data[0],
        symbol=symbol.upper().replace("-", ""),
        exchange=ExchangeName.COINBASE,
        interval=interval,
    )


def normalize_kraken_trade(trade_data: List[Any], symbol: str) -> Trade:
    """Convert Kraken trade format to unified Trade.

    Args:
        trade_data: Raw trade data from Kraken API [price, volume, time, side, orderType, misc]
        symbol: Trading symbol

    Returns:
        Unified Trade object
    """
    # Kraken format: [price, volume, time, side, orderType, misc]
    side = TradeSide.BUY if trade_data[3] == "b" else TradeSide.SELL

    return Trade(
        timestamp=trade_data[2],
        price=Decimal(trade_data[0]),
        quantity=Decimal(trade_data[1]),
        side=side,
        exchange=ExchangeName.KRAKEN,
        symbol=symbol.upper(),
        trade_id=None,  # Kraken doesn't provide trade ID in this format
    )


def normalize_kraken_orderbook(orderbook_data: Dict[str, Any], symbol: str) -> OrderBook:
    """Convert Kraken orderbook format to unified OrderBook.

    Args:
        orderbook_data: Raw orderbook data from Kraken API
        symbol: Trading symbol

    Returns:
        Unified OrderBook object
    """
    # Get the first (and usually only) pair data
    pair_data = list(orderbook_data.values())[0] if orderbook_data else {}

    return OrderBook(
        bids=[[Decimal(p), Decimal(q)] for p, q, _ in pair_data.get("bids", [])],
        asks=[[Decimal(p), Decimal(q)] for p, q, _ in pair_data.get("asks", [])],
        timestamp=time.time(),
        symbol=symbol.upper(),
        exchange=ExchangeName.KRAKEN,
    )


def normalize_kraken_ticker(ticker_data: Dict[str, Any], symbol: str) -> Ticker:
    """Convert Kraken ticker format to unified Ticker.

    Args:
        ticker_data: Raw ticker data from Kraken API
        symbol: Trading symbol

    Returns:
        Unified Ticker object
    """
    # Get the first pair data
    pair_data = list(ticker_data.values())[0] if ticker_data else {}

    # Kraken format: a=ask, b=bid, c=last, v=volume, p=weighted avg, etc.
    bid = Decimal(pair_data.get("b", [0])[0])
    ask = Decimal(pair_data.get("a", [0])[0])
    last = Decimal(pair_data.get("c", [0])[0])
    volume = Decimal(pair_data.get("v", [0, 0])[1])  # 24h volume

    # Calculate change_pct if we have opening price
    open_price = Decimal(pair_data.get("o", 0))
    change_pct = Decimal("0")
    if open_price > 0:
        change_pct = ((last - open_price) / open_price) * 100

    return Ticker(
        symbol=symbol.upper(),
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        change_pct=change_pct,
        timestamp=time.time(),
        exchange=ExchangeName.KRAKEN,
        high_24h=Decimal(pair_data.get("h", [0, 0])[1]) or None,
        low_24h=Decimal(pair_data.get("l", [0, 0])[1]) or None,
    )


def normalize_kraken_candle(candle_data: List[Any], symbol: str, interval: str) -> Candle:
    """Convert Kraken candle format to unified Candle.

    Args:
        candle_data: Raw candle data from Kraken API
                    [time, open, high, low, close, vwap, volume, count]
        symbol: Trading symbol
        interval: Candle interval

    Returns:
        Unified Candle object
    """
    # Kraken format: [time, open, high, low, close, vwap, volume, count]
    return Candle(
        open=Decimal(candle_data[1]),
        high=Decimal(candle_data[2]),
        low=Decimal(candle_data[3]),
        close=Decimal(candle_data[4]),
        volume=Decimal(candle_data[6]),
        timestamp=candle_data[0],
        symbol=symbol.upper(),
        exchange=ExchangeName.KRAKEN,
        interval=interval,
        trades_count=int(candle_data[7]) if len(candle_data) > 7 else None,
    )


# ============================================================================
# Generic normalization dispatcher
# ============================================================================

NORMALIZATION_MAP = {
    ExchangeName.BINANCE: {
        "trade": normalize_binance_trade,
        "orderbook": normalize_binance_orderbook,
        "ticker": normalize_binance_ticker,
        "candle": normalize_binance_candle,
    },
    ExchangeName.COINBASE: {
        "trade": normalize_coinbase_trade,
        "orderbook": normalize_coinbase_orderbook,
        "ticker": normalize_coinbase_ticker,
        "candle": normalize_coinbase_candle,
    },
    ExchangeName.KRAKEN: {
        "trade": normalize_kraken_trade,
        "orderbook": normalize_kraken_orderbook,
        "ticker": normalize_kraken_ticker,
        "candle": normalize_kraken_candle,
    },
}


def normalize(
    data: Any, data_type: str, exchange: ExchangeName, symbol: str, interval: Optional[str] = None
) -> Any:
    """Generic normalization dispatcher.

    Args:
        data: Raw exchange data
        data_type: Type of data ('trade', 'orderbook', 'ticker', 'candle')
        exchange: Exchange name
        symbol: Trading symbol
        interval: Candle interval (for candle data)

    Returns:
        Normalized data object
    """
    if exchange not in NORMALIZATION_MAP:
        raise ValueError(f"Unsupported exchange: {exchange}")

    if data_type not in NORMALIZATION_MAP[exchange]:
        raise ValueError(f"Unsupported data type '{data_type}' for exchange {exchange}")

    normalizer = NORMALIZATION_MAP[exchange][data_type]

    if data_type == "candle" and interval:
        return normalizer(data, symbol, interval)
    return normalizer(data, symbol)
