"""SMA Crossover Strategy using BaseAlgorithm."""

from typing import Optional, Dict, Any, List
from decimal import Decimal

import pandas as pd
import numpy as np

from algorithms.base_algorithm import BaseAlgorithm, AlgorithmConfig, Signal
from adapters.base_adapter import Order, OrderSide, OrderType


class SmaCrossoverStrategy(BaseAlgorithm):
    """
    Simple Moving Average Crossover Strategy.
    
    Generates buy signals when fast SMA crosses above slow SMA.
    Generates sell signals when fast SMA crosses below slow SMA.
    
    Parameters:
        fast_period: Period for fast moving average (default: 20)
        slow_period: Period for slow moving average (default: 50)
        stop_loss_pct: Stop loss percentage (default: 2.0)
        take_profit_pct: Take profit percentage (default: 4.0)
    """
    
    def __init__(
        self,
        symbols: List[str],
        fast_period: int = 20,
        slow_period: int = 50,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 4.0,
        **kwargs
    ):
        config = AlgorithmConfig(
            name=f"SMA_Crossover_{fast_period}_{slow_period}",
            symbols=symbols,
            timeframe="1h",
            lookback_periods=max(fast_period, slow_period) + 10,
            use_bracket_orders=True
        )
        
        super().__init__(config=config, **kwargs)
        
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        
        # Strategy state
        self._signals: Dict[str, str] = {}  # symbol -> last signal
        self._indicators: Dict[str, pd.DataFrame] = {}
    
    def initialize(self, data: Dict[str, pd.DataFrame]) -> None:
        """Initialize the strategy with historical data."""
        for symbol, df in data.items():
            if len(df) < self.slow_period:
                self.logger.logger.warning(f"Insufficient data for {symbol}")
                continue
            
            # Calculate SMAs
            indicators = df.copy()
            indicators['sma_fast'] = indicators['close'].rolling(window=self.fast_period).mean()
            indicators['sma_slow'] = indicators['close'].rolling(window=self.slow_period).mean()
            indicators['signal'] = np.where(
                indicators['sma_fast'] > indicators['sma_slow'], 1, -1
            )
            
            self._indicators[symbol] = indicators
            
            # Set initial signal state
            if len(indicators) >= 2:
                last_signal = indicators['signal'].iloc[-1]
                self._signals[symbol] = 'bullish' if last_signal > 0 else 'bearish'
        
        self._initialized = True
        self.state = AlgorithmState.READY
        self.logger.logger.info(
            f"SMA Crossover initialized: fast={self.fast_period}, slow={self.slow_period}"
        )
    
    def on_data(self, data: Dict[str, pd.DataFrame]) -> Optional[Signal]:
        """Generate signals based on SMA crossover."""
        for symbol, df in data.items():
            if len(df) < self.slow_period:
                continue
            
            # Calculate SMAs
            df['sma_fast'] = df['close'].rolling(window=self.fast_period).mean()
            df['sma_slow'] = df['close'].rolling(window=self.slow_period).mean()
            
            if len(df) < 2:
                continue
            
            # Get current and previous states
            curr_fast = df['sma_fast'].iloc[-1]
            curr_slow = df['sma_slow'].iloc[-1]
            prev_fast = df['sma_fast'].iloc[-2]
            prev_slow = df['sma_slow'].iloc[-2]
            
            # Check for valid values
            if pd.isna(curr_fast) or pd.isna(curr_slow) or pd.isna(prev_fast) or pd.isna(prev_slow):
                continue
            
            curr_bullish = curr_fast > curr_slow
            prev_bullish = prev_fast > prev_slow
            
            # Detect crossover
            if curr_bullish != prev_bullish:
                current_price = Decimal(str(df['close'].iloc[-1]))
                
                if curr_bullish:
                    # Bullish crossover (fast above slow)
                    confidence = min(abs(curr_fast - curr_slow) / curr_slow * 10, 1.0)
                    
                    return Signal(
                        symbol=symbol,
                        action='buy',
                        timestamp=pd.Timestamp.now(),
                        price=current_price,
                        confidence=confidence,
                        order_type=OrderType.MARKET,
                        stop_loss_pct=self.stop_loss_pct,
                        take_profit_pct=self.take_profit_pct,
                        metadata={
                            'crossover': 'bullish',
                            'sma_fast': float(curr_fast),
                            'sma_slow': float(curr_slow),
                            'strategy': 'sma_crossover'
                        }
                    )
                else:
                    # Bearish crossover (fast below slow)
                    confidence = min(abs(curr_fast - curr_slow) / curr_slow * 10, 1.0)
                    
                    return Signal(
                        symbol=symbol,
                        action='sell',
                        timestamp=pd.Timestamp.now(),
                        price=current_price,
                        confidence=confidence,
                        order_type=OrderType.MARKET,
                        metadata={
                            'crossover': 'bearish',
                            'sma_fast': float(curr_fast),
                            'sma_slow': float(curr_slow),
                            'strategy': 'sma_crossover'
                        }
                    )
        
        return None
    
    async def on_execute(self, signal: Signal) -> Optional[Order]:
        """Execute SMA crossover signal."""
        side = OrderSide.BUY if signal.action == 'buy' else OrderSide.SELL
        
        order = await self.execute_trade(
            symbol=signal.symbol,
            side=side,
            order_type=signal.order_type,
            stop_loss_pct=signal.stop_loss_pct,
            take_profit_pct=signal.take_profit_pct
        )
        
        return order
    
    async def on_order_filled(self, order: Order) -> None:
        """Handle order fill."""
        # Update positions tracking
        if order.side == OrderSide.BUY:
            self.positions[order.symbol] = self.positions.get(order.symbol, Decimal("0")) + order.filled_quantity
        else:
            self.positions[order.symbol] = self.positions.get(order.symbol, Decimal("0")) - order.filled_quantity
        
        self.logger.logger.info(
            f"SMA Crossover: {order.side.value.upper()} {order.filled_quantity} {order.symbol} "
            f"@ {order.avg_fill_price}"
        )
    
    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        base_params = super().get_parameters()
        base_params.update({
            'fast_period': self.fast_period,
            'slow_period': self.slow_period,
            'stop_loss_pct': self.stop_loss_pct,
            'take_profit_pct': self.take_profit_pct
        })
        return base_params
    
    def set_parameters(self, params: Dict[str, Any]) -> None:
        """Update strategy parameters."""
        if 'fast_period' in params:
            self.fast_period = params['fast_period']
        if 'slow_period' in params:
            self.slow_period = params['slow_period']
        if 'stop_loss_pct' in params:
            self.stop_loss_pct = params['stop_loss_pct']
        if 'take_profit_pct' in params:
            self.take_profit_pct = params['take_profit_pct']
        
        self.logger.logger.info(f"Parameters updated: {params}")


# Example QuantConnect-style algorithm using the adapter
class QCSmaCrossover(QCAlgorithmInterface):
    """QuantConnect-style SMA Crossover implementation."""
    
    def initialize(self) -> None:
        """Initialize the algorithm."""
        self.fast_period = 20
        self.slow_period = 50
        self.log("SMA Crossover algorithm initialized")
    
    def on_data(self, slice_data: Slice) -> None:
        """Process data."""
        for symbol in slice_data.keys:
            if not self.securities.contains_key(symbol):
                continue
            
            # Get historical data (in real QC, you'd use History())
            # Here we simulate with current bar
            bar = slice_data[symbol]
            
            # Simplified: use current price as proxy
            price = float(bar.get('close', 0))
            
            holdings = self.portfolio[symbol].holdings if self.portfolio else None
            
            # Simple logic: buy if not invested
            if holdings and not holdings.is_long and price > 0:
                self.set_holdings(symbol, 1.0)
                self.log(f"Buying {symbol} at {price}")
            elif holdings and holdings.is_long and price > holdings.average_price * 1.05:
                self.liquidate(symbol)
                self.log(f"Selling {symbol} at {price}")
