"""Broker API wrappers for various trading platforms."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float


class BaseBroker(ABC):
    """Abstract base class for broker implementations."""
    
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self._connected = False
    
    @abstractmethod
    def connect(self) -> None:
        """Connect to broker API."""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from broker API."""
        pass
    
    @abstractmethod
    def get_account(self) -> Dict[str, Any]:
        """Get account information."""
        pass
    
    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get current positions."""
        pass
    
    @abstractmethod
    def submit_order(self, order: Order) -> Dict[str, Any]:
        """Submit an order."""
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        pass
    
    @abstractmethod
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get order status."""
        pass


try:
    from .alpaca import AlpacaBroker
    from .binance import BinanceBroker
except Exception:  # pragma: no cover - optional import fallback
    AlpacaBroker = None
    BinanceBroker = None


__all__ = [
    "OrderType",
    "OrderSide",
    "Order",
    "Position",
    "BaseBroker",
    "AlpacaBroker",
    "BinanceBroker",
]
