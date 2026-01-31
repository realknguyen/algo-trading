"""Exchange adapters for trading system."""

from adapters.base_adapter import (
    BaseExchangeAdapter,
    Order,
    OrderType,
    OrderSide,
    OrderStatus,
    TimeInForce,
    Ticker,
    Position,
    Balance,
    ExchangeError,
    AuthenticationError,
    InsufficientFundsError,
    InvalidSymbolError
)

from adapters.binance import BinanceAdapter
from adapters.kraken import KrakenAdapter
from adapters.coinbase import CoinbaseAdapter

__all__ = [
    # Base classes
    'BaseExchangeAdapter',
    'Order',
    'OrderType',
    'OrderSide',
    'OrderStatus',
    'TimeInForce',
    'Ticker',
    'Position',
    'Balance',
    # Exceptions
    'ExchangeError',
    'AuthenticationError',
    'InsufficientFundsError',
    'InvalidSymbolError',
    # Adapters
    'BinanceAdapter',
    'KrakenAdapter',
    'CoinbaseAdapter'
]
