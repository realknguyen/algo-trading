"""QuantConnect adapter for running QC algorithms in this framework."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Dict, Any, List, Callable, Union
from datetime import datetime
from enum import Enum

import pandas as pd
import numpy as np

from algorithms.base_algorithm import BaseAlgorithm, AlgorithmConfig, Signal, AlgorithmState
from adapters.base_adapter import Order, OrderSide, OrderType
from log_config import TradingLogger


class Resolution(Enum):
    """Data resolution enumeration (QuantConnect style)."""
    TICK = "tick"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAILY = "daily"


@dataclass
class QCAlgorithmConfig(AlgorithmConfig):
    """QuantConnect-style algorithm configuration."""
    resolution: Resolution = Resolution.HOUR
    benchmark_symbol: str = "SPY"
    
    # QC-specific settings
    warm_up_periods: int = 50
    use_fill_forward: bool = True
    
    # Universe settings
    universe_resolution: Optional[Resolution] = None


class Slice:
    """QuantConnect-style data slice.
    
    Emulates QC's Slice object containing all data for a single time step.
    """
    
    def __init__(self, timestamp: datetime, data: Dict[str, pd.Series]):
        self.time = timestamp
        self._data = data
    
    def __getitem__(self, symbol: str) -> pd.Series:
        """Get data for a symbol."""
        return self._data.get(symbol)
    
    def contains_key(self, symbol: str) -> bool:
        """Check if slice contains data for symbol."""
        return symbol in self._data
    
    def get(self, symbol: str) -> Optional[pd.Series]:
        """Get data for symbol, returns None if not found."""
        return self._data.get(symbol)
    
    @property
    def keys(self) -> List[str]:
        """Get all symbols in slice."""
        return list(self._data.keys())
    
    @property
    def bars(self) -> Dict[str, pd.Series]:
        """Get all price bars (OHLCV)."""
        return self._data


class Security:
    """QuantConnect-style security object."""
    
    def __init__(self, symbol: str, price: float, data: pd.Series):
        self.symbol = symbol
        self.price = price
        self._data = data
        self.holdings = Holdings()
    
    @property
    def close(self) -> float:
        """Get closing price."""
        return float(self._data.get('close', self.price))
    
    @property
    def open(self) -> float:
        """Get opening price."""
        return float(self._data.get('open', self.price))
    
    @property
    def high(self) -> float:
        """Get high price."""
        return float(self._data.get('high', self.price))
    
    @property
    def low(self) -> float:
        """Get low price."""
        return float(self._data.get('low', self.price))
    
    @property
    def volume(self) -> float:
        """Get volume."""
        return float(self._data.get('volume', 0))


class Holdings:
    """QuantConnect-style holdings object."""
    
    def __init__(self):
        self.quantity = 0.0
        self.average_price = 0.0
        self.hold_stock = False
    
    @property
    def is_long(self) -> bool:
        """Check if holding long position."""
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        """Check if holding short position."""
        return self.quantity < 0
    
    @property
    def absolute_quantity(self) -> float:
        """Get absolute quantity."""
        return abs(self.quantity)


class Portfolio:
    """QuantConnect-style portfolio manager."""
    
    def __init__(self, initial_cash: float = 100000.0):
        self.cash = initial_cash
        self.total_portfolio_value = initial_cash
        self._securities: Dict[str, Security] = {}
        self._holdings: Dict[str, Holdings] = {}
    
    def add_security(self, symbol: str, security: Security) -> None:
        """Add security to portfolio."""
        self._securities[symbol] = security
        if symbol not in self._holdings:
            self._holdings[symbol] = Holdings()
        security.holdings = self._holdings[symbol]
    
    def __getitem__(self, symbol: str) -> Security:
        """Get security by symbol."""
        return self._securities.get(symbol)
    
    def contains_key(self, symbol: str) -> bool:
        """Check if portfolio contains security."""
        return symbol in self._securities
    
    def invested(self) -> bool:
        """Check if portfolio has any holdings."""
        return any(h.quantity != 0 for h in self._holdings.values())
    
    def total_unrealised_profit(self) -> float:
        """Get total unrealized P&L."""
        return 0.0  # Simplified
    
    def total_holdings_value(self) -> float:
        """Get total value of holdings."""
        total = 0.0
        for symbol, holdings in self._holdings.items():
            if symbol in self._securities:
                total += holdings.quantity * self._securities[symbol].price
        return total


class QCAlgorithmInterface(ABC):
    """QuantConnect algorithm interface.
    
    This is the interface that QuantConnect algorithms implement.
    Algorithms inheriting from this can be wrapped by QuantConnectAdapter.
    """
    
    def __init__(self):
        self.portfolio: Optional[Portfolio] = None
        self.time: Optional[datetime] = None
        self.securities: Dict[str, Security] = {}
        self._indicators: Dict[str, Any] = {}
        self._orders: List[Dict[str, Any]] = []
        
        # Settings
        self.warm_up_indicator = 0
    
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the algorithm."""
        pass
    
    @abstractmethod
    def on_data(self, slice_data: Slice) -> None:
        """Process new data."""
        pass
    
    def set_holdings(self, symbol: str, percentage: float) -> None:
        """Set portfolio holdings for a symbol.
        
        Args:
            symbol: Trading symbol
            percentage: Target portfolio percentage (0-1)
        """
        if self.portfolio:
            target_value = self.portfolio.total_portfolio_value * percentage
            if symbol in self.securities:
                target_shares = target_value / self.securities[symbol].price
                # Record the order intent
                self._orders.append({
                    'type': 'set_holdings',
                    'symbol': symbol,
                    'percentage': percentage,
                    'target_shares': target_shares,
                    'timestamp': self.time
                })
    
    def market_order(self, symbol: str, quantity: float) -> None:
        """Place a market order.
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity (positive=buy, negative=sell)
        """
        self._orders.append({
            'type': 'market',
            'symbol': symbol,
            'quantity': quantity,
            'timestamp': self.time
        })
    
    def limit_order(self, symbol: str, quantity: float, limit_price: float) -> None:
        """Place a limit order.
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity
            limit_price: Limit price
        """
        self._orders.append({
            'type': 'limit',
            'symbol': symbol,
            'quantity': quantity,
            'limit_price': limit_price,
            'timestamp': self.time
        })
    
    def stop_market_order(self, symbol: str, quantity: float, stop_price: float) -> None:
        """Place a stop market order.
        
        Args:
            symbol: Trading symbol
            quantity: Order quantity
            stop_price: Stop trigger price
        """
        self._orders.append({
            'type': 'stop_market',
            'symbol': symbol,
            'quantity': quantity,
            'stop_price': stop_price,
            'timestamp': self.time
        })
    
    def liquidate(self, symbol: Optional[str] = None) -> None:
        """Liquidate holdings.
        
        Args:
            symbol: Symbol to liquidate (None = all)
        """
        self._orders.append({
            'type': 'liquidate',
            'symbol': symbol,
            'timestamp': self.time
        })
    
    def log(self, message: str) -> None:
        """Log a message."""
        print(f"[{self.time}] {message}")
    
    def debug(self, message: str) -> None:
        """Log debug message."""
        print(f"[DEBUG] [{self.time}] {message}")
    
    def plot(self, chart: str, series: str, value: float) -> None:
        """Plot a value (simplified - logs to console)."""
        print(f"[PLOT] {chart}/{series}: {value}")


class QuantConnectAdapter(BaseAlgorithm):
    """Adapter to run QuantConnect algorithms in this framework.
    
    Wraps a QCAlgorithmInterface implementation and translates:
    - QC Slice objects → pandas DataFrames
    - QC Portfolio methods → Signal objects
    - QC orders → OrderManager requests
    """
    
    def __init__(
        self,
        qc_algorithm: QCAlgorithmInterface,
        config: QCAlgorithmConfig,
        **kwargs
    ):
        super().__init__(config=config, **kwargs)
        self.qc_algorithm = qc_algorithm
        self.qc_config = config
        self.logger = TradingLogger(f"QCAdapter.{config.name}")
        
        # Setup QC environment
        self.qc_algorithm.portfolio = Portfolio(
            initial_cash=float(self.risk_manager.current_equity) if self.risk_manager else 100000.0
        )
        
        # Track orders from QC algorithm
        self.pending_qc_orders: List[Dict[str, Any]] = []
        
        # Track previous holdings to detect changes
        self.previous_holdings: Dict[str, float] = {}
    
    def initialize(self, data: Dict[str, pd.DataFrame]) -> None:
        """Initialize QC algorithm and setup securities."""
        # Create securities from data
        for symbol, df in data.items():
            if not df.empty:
                latest = df.iloc[-1]
                price = float(latest.get('close', latest.get('Close', 0)))
                
                security = Security(symbol, price, latest)
                self.qc_algorithm.securities[symbol] = security
                if self.qc_algorithm.portfolio:
                    self.qc_algorithm.portfolio.add_security(symbol, security)
        
        # Call QC Initialize
        try:
            self.qc_algorithm.initialize()
            self._initialized = True
            self.state = AlgorithmState.READY
            self.logger.logger.info("QuantConnect algorithm initialized")
        except Exception as e:
            self.logger.error("initialize", f"QC Initialize failed: {e}")
            self.state = AlgorithmState.ERROR
    
    def on_data(self, data: Dict[str, pd.DataFrame]) -> Optional[Signal]:
        """Process data and call QC OnData."""
        if not self._initialized:
            return None
        
        # Update QC time
        latest_time = None
        for symbol, df in data.items():
            if not df.empty:
                latest_time = df.index[-1]
                break
        
        if latest_time is None:
            return None
        
        self.qc_algorithm.time = latest_time if isinstance(latest_time, datetime) else datetime.now()
        
        # Update securities with latest data
        slice_data = {}
        for symbol, df in data.items():
            if not df.empty:
                latest = df.iloc[-1]
                price = float(latest.get('close', latest.get('Close', 0)))
                
                # Update security
                if symbol in self.qc_algorithm.securities:
                    self.qc_algorithm.securities[symbol]._data = latest
                    self.qc_algorithm.securities[symbol].price = price
                else:
                    security = Security(symbol, price, latest)
                    self.qc_algorithm.securities[symbol] = security
                    if self.qc_algorithm.portfolio:
                        self.qc_algorithm.portfolio.add_security(symbol, security)
                
                slice_data[symbol] = latest
        
        # Create QC Slice
        qc_slice = Slice(self.qc_algorithm.time, slice_data)
        
        # Clear previous orders
        self.qc_algorithm._orders.clear()
        
        # Call QC OnData
        try:
            self.qc_algorithm.on_data(qc_slice)
        except Exception as e:
            self.logger.error("on_data", f"QC OnData error: {e}")
        
        # Detect signals from QC orders
        signal = self._detect_signal_from_orders()
        
        return signal
    
    def _detect_signal_from_orders(self) -> Optional[Signal]:
        """Detect trading signal from QC orders."""
        if not self.qc_algorithm._orders:
            return None
        
        # Get the most recent order
        order = self.qc_algorithm._orders[-1]
        
        symbol = order['symbol']
        order_type = order['type']
        
        # Determine action and quantity
        if order_type == 'set_holdings':
            # Calculate required action
            target_pct = order['percentage']
            current_holding = self.previous_holdings.get(symbol, 0.0)
            
            # Get security price
            security = self.qc_algorithm.securities.get(symbol)
            if not security:
                return None
            
            portfolio_value = self.qc_algorithm.portfolio.total_portfolio_value if self.qc_algorithm.portfolio else 0
            target_shares = (portfolio_value * target_pct) / security.price
            
            quantity_diff = target_shares - current_holding
            
            if abs(quantity_diff) < 0.0001:
                return None
            
            action = 'buy' if quantity_diff > 0 else 'sell'
            
            signal = Signal(
                symbol=symbol,
                action=action,
                timestamp=order['timestamp'],
                price=Decimal(str(security.price)),
                confidence=1.0,
                metadata={
                    'qc_order_type': 'set_holdings',
                    'target_percentage': target_pct,
                    'quantity': abs(quantity_diff)
                }
            )
            
            # Update tracking
            self.previous_holdings[symbol] = target_shares
            
            return signal
        
        elif order_type == 'market':
            quantity = order['quantity']
            if quantity == 0:
                return None
            
            action = 'buy' if quantity > 0 else 'sell'
            
            security = self.qc_algorithm.securities.get(symbol)
            price = Decimal(str(security.price)) if security else Decimal("0")
            
            return Signal(
                symbol=symbol,
                action=action,
                timestamp=order['timestamp'],
                price=price,
                order_type=OrderType.MARKET,
                quantity=Decimal(str(abs(quantity))),
                metadata={'qc_order_type': 'market'}
            )
        
        elif order_type == 'liquidate':
            if symbol is None:
                # Liquidate all - return signal for first holding
                for sym, qty in self.previous_holdings.items():
                    if qty != 0:
                        security = self.qc_algorithm.securities.get(sym)
                        return Signal(
                            symbol=sym,
                            action='sell' if qty > 0 else 'buy',
                            timestamp=order['timestamp'],
                            price=Decimal(str(security.price)) if security else Decimal("0"),
                            metadata={'qc_order_type': 'liquidate'}
                        )
            else:
                qty = self.previous_holdings.get(symbol, 0)
                if qty != 0:
                    security = self.qc_algorithm.securities.get(symbol)
                    return Signal(
                        symbol=symbol,
                        action='sell' if qty > 0 else 'buy',
                        timestamp=order['timestamp'],
                        price=Decimal(str(security.price)) if security else Decimal("0"),
                        metadata={'qc_order_type': 'liquidate'}
                    )
        
        return None
    
    async def on_execute(self, signal: Signal) -> Optional[Any]:
        """Execute QC-generated signal."""
        # Use base class execute_trade
        side = OrderSide.BUY if signal.action == 'buy' else OrderSide.SELL
        
        order = await self.execute_trade(
            symbol=signal.symbol,
            side=side,
            quantity=signal.quantity,
            order_type=signal.order_type
        )
        
        return order
    
    async def on_order_filled(self, order: Order) -> None:
        """Handle order fill from QC algorithm."""
        # Update QC portfolio
        if self.qc_algorithm.portfolio and order.symbol in self.qc_algorithm.securities:
            security = self.qc_algorithm.securities[order.symbol]
            
            if order.side == OrderSide.BUY:
                security.holdings.quantity += float(order.filled_quantity)
            else:
                security.holdings.quantity -= float(order.filled_quantity)
            
            # Update average price
            if security.holdings.quantity > 0:
                security.holdings.average_price = float(order.avg_fill_price or order.price or 0)
            
            security.holdings.hold_stock = security.holdings.quantity != 0
        
        self.logger.logger.info(f"QC order filled: {order.symbol} {order.side.value} {order.filled_quantity}")
    
    def get_parameters(self) -> Dict[str, Any]:
        """Get QC algorithm parameters."""
        base_params = super().get_parameters()
        base_params.update({
            'resolution': self.qc_config.resolution.value,
            'benchmark': self.qc_config.benchmark_symbol,
            'warm_up_periods': self.qc_config.warm_up_periods
        })
        return base_params
