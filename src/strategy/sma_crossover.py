"""SMA Crossover Strategy - Sample implementation."""

from typing import Optional, Dict, Any
import pandas as pd
from . import BaseStrategy, Signal


class SmaCrossoverStrategy(BaseStrategy):
    """
    Simple Moving Average Crossover Strategy.
    
    Generates buy signals when fast SMA crosses above slow SMA.
    Generates sell signals when fast SMA crosses below slow SMA.
    
    Parameters:
        fast_period: Period for fast moving average (default: 20)
        slow_period: Period for slow moving average (default: 50)
    """
    
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        default_params = {
            'fast_period': 20,
            'slow_period': 50,
        }
        if params:
            default_params.update(params)
        super().__init__(name="SMA_Crossover", params=default_params)
        self._prev_state = None
    
    def initialize(self, data: pd.DataFrame) -> None:
        """Pre-calculate indicators on historical data."""
        fast = self.params['fast_period']
        slow = self.params['slow_period']
        
        data['sma_fast'] = data['close'].rolling(window=fast).mean()
        data['sma_slow'] = data['close'].rolling(window=slow).mean()
        
        # Determine initial state
        if len(data) >= 2:
            last_row = data.iloc[-1]
            prev_row = data.iloc[-2]
            
            if pd.notna(last_row['sma_fast']) and pd.notna(last_row['sma_slow']):
                self._prev_state = last_row['sma_fast'] > last_row['sma_slow']
        
        self._initialized = True
    
    def on_data(self, data: pd.DataFrame) -> Optional[Signal]:
        """Generate signals based on SMA crossover."""
        if len(data) < self.params['slow_period']:
            return None
        
        fast = self.params['fast_period']
        slow = self.params['slow_period']
        
        # Calculate SMAs
        data['sma_fast'] = data['close'].rolling(window=fast).mean()
        data['sma_slow'] = data['close'].rolling(window=slow).mean()
        
        if len(data) < 2:
            return None
        
        # Get last two data points
        curr = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Check if we have valid SMA values
        if pd.isna(curr['sma_fast']) or pd.isna(curr['sma_slow']):
            return None
        
        # Determine current state
        curr_state = curr['sma_fast'] > curr['sma_slow']
        prev_state = prev['sma_fast'] > prev['sma_slow'] if pd.notna(prev['sma_fast']) else None
        
        signal = None
        
        # Crossover detection
        if prev_state is not None and curr_state != prev_state:
            if curr_state:  # Fast crossed above slow
                signal = Signal(
                    symbol=curr.get('symbol', 'UNKNOWN'),
                    action='buy',
                    timestamp=curr.name if isinstance(curr.name, pd.Timestamp) else pd.Timestamp.now(),
                    price=curr['close'],
                    confidence=abs(curr['sma_fast'] - curr['sma_slow']) / curr['sma_slow'],
                    metadata={
                        'sma_fast': curr['sma_fast'],
                        'sma_slow': curr['sma_slow'],
                        'crossover': 'bullish'
                    }
                )
            else:  # Fast crossed below slow
                signal = Signal(
                    symbol=curr.get('symbol', 'UNKNOWN'),
                    action='sell',
                    timestamp=curr.name if isinstance(curr.name, pd.Timestamp) else pd.Timestamp.now(),
                    price=curr['close'],
                    confidence=abs(curr['sma_fast'] - curr['sma_slow']) / curr['sma_slow'],
                    metadata={
                        'sma_fast': curr['sma_fast'],
                        'sma_slow': curr['sma_slow'],
                        'crossover': 'bearish'
                    }
                )
        
        return signal
    
    def get_parameters(self) -> Dict[str, Any]:
        """Return current parameters."""
        return self.params.copy()


if __name__ == "__main__":
    # Quick test
    import numpy as np
    
    # Generate sample data
    dates = pd.date_range('2023-01-01', periods=100, freq='D')
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    df = pd.DataFrame({
        'close': prices,
        'symbol': 'TEST'
    }, index=dates)
    
    strategy = SmaCrossoverStrategy()
    strategy.initialize(df)
    
    signal = strategy.on_data(df)
    if signal:
        print(f"Signal: {signal.action} at {signal.price:.2f}")
    else:
        print("No signal generated")
