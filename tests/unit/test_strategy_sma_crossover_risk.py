"""Tests for SMA Crossover Strategy with Risk Management."""

import pytest
import pandas as pd
import numpy as np

from src.strategy.sma_crossover_risk import SmaCrossoverRiskStrategy


class TestSmaCrossoverRiskStrategy:
    """Test cases for SMA Crossover Risk Strategy."""
    
    def test_initialization_defaults(self):
        """Test strategy initializes with correct defaults."""
        strategy = SmaCrossoverRiskStrategy()
        
        assert strategy.name == "SMA_Crossover_Risk"
        assert strategy.params['fast_period'] == 10
        assert strategy.params['slow_period'] == 30
        assert strategy.params['stop_loss_pct'] == 2.0
        assert strategy.params['take_profit_pct'] == 6.0
        assert strategy.params['risk_per_trade'] == 0.02
        assert strategy.params['volatility_filter'] == 5.0
    
    def test_custom_parameters(self):
        """Test custom parameter override."""
        strategy = SmaCrossoverRiskStrategy({
            'fast_period': 5,
            'slow_period': 20,
            'stop_loss_pct': 3.0
        })
        
        assert strategy.params['fast_period'] == 5
        assert strategy.params['slow_period'] == 20
        assert strategy.params['stop_loss_pct'] == 3.0
        # Other params should remain defaults
        assert strategy.params['take_profit_pct'] == 6.0
    
    def test_initialize_sets_state(self, sample_ohlcv_data):
        """Test initialize sets up strategy state."""
        strategy = SmaCrossoverRiskStrategy()
        strategy.initialize(sample_ohlcv_data.iloc[:50])
        
        assert strategy._initialized is True
        assert strategy._prev_crossover_state is not None
    
    def test_bullish_crossover_generates_buy_signal(self, trending_data):
        """Test that bullish crossover generates buy signal."""
        strategy = SmaCrossoverRiskStrategy({
            'fast_period': 10,
            'slow_period': 30
        })
        
        # Initialize with first 100 bars (downtrend)
        strategy.initialize(trending_data.iloc[:100])
        
        # Continue through uptrend - should get buy signal
        signal = None
        for i in range(100, 150):
            result = strategy.on_data(trending_data.iloc[:i+1])
            if result and result.action == 'buy':
                signal = result
                break
        
        assert signal is not None
        assert signal.action == 'buy'
        assert signal.confidence > 0
        assert signal.confidence <= 1.0
    
    def test_bearish_crossover_generates_sell_signal(self):
        """Test that bearish crossover generates sell signal."""
        # Create data that switches from uptrend to downtrend
        dates = pd.date_range('2023-01-01', periods=200, freq='D')
        prices = []
        for i in range(200):
            if i < 100:
                prices.append(100 + i * 0.1)  # Uptrend
            else:
                prices.append(110 - (i - 100) * 0.15)  # Downtrend
        
        df = pd.DataFrame({
            'open': [p * 0.99 for p in prices],
            'high': [p * 1.02 for p in prices],
            'low': [p * 0.98 for p in prices],
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, 200),
            'symbol': 'TEST'
        }, index=dates)
        
        strategy = SmaCrossoverRiskStrategy({
            'fast_period': 10,
            'slow_period': 30
        })
        
        strategy.initialize(df.iloc[:100])
        
        # Look for sell signal in downtrend
        signal = None
        for i in range(100, 150):
            result = strategy.on_data(df.iloc[:i+1])
            if result and result.action == 'sell':
                signal = result
                break
        
        assert signal is not None
        assert signal.action == 'sell'
    
    def test_signal_includes_risk_metadata(self, trending_data):
        """Test that signals include risk management metadata."""
        strategy = SmaCrossoverRiskStrategy()
        strategy.initialize(trending_data.iloc[:100])
        
        signal = None
        for i in range(100, 150):
            result = strategy.on_data(trending_data.iloc[:i+1])
            if result:
                signal = result
                break
        
        assert signal is not None
        assert 'stop_loss_price' in signal.metadata
        assert 'take_profit_price' in signal.metadata
        assert 'volatility_pct' in signal.metadata
        assert 'sma_fast' in signal.metadata
        assert 'sma_slow' in signal.metadata
        assert 'atr' in signal.metadata
    
    def test_volatility_filter_blocks_signals(self, sample_ohlcv_data):
        """Test that high volatility blocks signal generation."""
        # Create high volatility data
        high_vol_data = sample_ohlcv_data.copy()
        high_vol_data['high'] = high_vol_data['close'] * 1.2
        high_vol_data['low'] = high_vol_data['close'] * 0.8
        
        strategy = SmaCrossoverRiskStrategy({
            'volatility_filter': 2.0  # Very low threshold
        })
        strategy.initialize(high_vol_data.iloc[:50])
        
        # Should not generate signals due to high volatility
        signal = strategy.on_data(high_vol_data)
        assert signal is None
    
    def test_position_tracking(self, trending_data):
        """Test that strategy tracks position state."""
        strategy = SmaCrossoverRiskStrategy()
        strategy.initialize(trending_data.iloc[:100])
        
        # Initially flat
        assert strategy._position == 0
        
        # Find buy signal
        for i in range(100, 150):
            result = strategy.on_data(trending_data.iloc[:i+1])
            if result and result.action == 'buy':
                assert strategy._position == 1
                assert strategy._entry_price is not None
                break
    
    def test_state_serialization(self, trending_data):
        """Test state serialization and deserialization."""
        strategy = SmaCrossoverRiskStrategy()
        strategy.initialize(trending_data.iloc[:100])
        
        # Run some data to set state
        for i in range(100, 110):
            strategy.on_data(trending_data.iloc[:i+1])
        
        # Get state
        state = strategy.get_state()
        
        # Create new strategy and restore state
        new_strategy = SmaCrossoverRiskStrategy()
        new_strategy.set_state(state)
        
        assert new_strategy._initialized == strategy._initialized
        assert new_strategy._position == strategy._position
        assert new_strategy._entry_price == strategy._entry_price
    
    def test_get_parameters_returns_copy(self):
        """Test that get_parameters returns a copy."""
        strategy = SmaCrossoverRiskStrategy({'fast_period': 5})
        params = strategy.get_parameters()
        
        # Modify returned params
        params['fast_period'] = 999
        
        # Original should be unchanged
        assert strategy.params['fast_period'] == 5
    
    def test_signal_confidence_bounds(self, trending_data):
        """Test that signal confidence is between 0 and 1."""
        strategy = SmaCrossoverRiskStrategy()
        strategy.initialize(trending_data.iloc[:100])
        
        # Collect all signals
        for i in range(100, len(trending_data)):
            result = strategy.on_data(trending_data.iloc[:i+1])
            if result:
                assert 0 <= result.confidence <= 1.0
    
    def test_not_enough_data_returns_none(self):
        """Test that strategy returns None with insufficient data."""
        strategy = SmaCrossoverRiskStrategy({'slow_period': 50})
        
        # Only 10 bars, need 50
        dates = pd.date_range('2023-01-01', periods=10, freq='D')
        df = pd.DataFrame({
            'open': [100] * 10,
            'high': [102] * 10,
            'low': [98] * 10,
            'close': [100] * 10,
            'volume': [1000000] * 10,
            'symbol': 'TEST'
        }, index=dates)
        
        signal = strategy.on_data(df)
        assert signal is None
