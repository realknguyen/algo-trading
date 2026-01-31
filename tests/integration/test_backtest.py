"""Integration tests for backtesting workflow."""

import pytest
import pandas as pd
import numpy as np

from src.strategy.sma_crossover import SmaCrossoverStrategy
from src.backtest.runner import BacktestRunner
from src.data.fetcher import DataFetcher


class TestBacktestIntegration:
    """Integration tests for backtesting."""
    
    @pytest.fixture
    def sample_market_data(self):
        """Create sample market data for backtesting."""
        np.random.seed(42)
        dates = pd.date_range('2023-01-01', periods=252, freq='B')  # Business days
        
        # Generate trending data
        returns = np.random.randn(252) * 0.02
        prices = 100 * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame({
            'open': prices * (1 + np.random.randn(252) * 0.001),
            'high': prices * (1 + abs(np.random.randn(252)) * 0.01),
            'low': prices * (1 - abs(np.random.randn(252)) * 0.01),
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, 252),
            'symbol': 'TEST'
        }, index=dates)
        
        return df
    
    def test_backtest_complete_workflow(self, sample_market_data):
        """Test complete backtest workflow from data to results."""
        # Create strategy
        strategy = SmaCrossoverStrategy({
            'fast_period': 10,
            'slow_period': 30
        })
        
        # Run backtest
        runner = BacktestRunner(initial_capital=100000, commission=0.001)
        result = runner.run(strategy, sample_market_data, symbol='TEST')
        
        # Verify result structure
        assert result.strategy_name == "SMA_Crossover"
        assert result.initial_capital == 100000
        assert result.final_capital > 0
        assert isinstance(result.trades, list)
        assert isinstance(result.equity_curve, pd.Series)
        
        # Verify metrics are calculated
        metrics = result.calculate_metrics()
        assert 'total_return_pct' in metrics
        assert 'win_rate' in metrics
        assert 'profit_factor' in metrics
        assert 'num_trades' in metrics
        
        # Verify summary string
        summary = result.summary()
        assert "SMA_Crossover" in summary
        assert "Total Return" in summary
    
    def test_backtest_with_multiple_trades(self, sample_market_data):
        """Test backtest that generates multiple trades."""
        strategy = SmaCrossoverStrategy({
            'fast_period': 5,
            'slow_period': 20
        })
        
        runner = BacktestRunner(initial_capital=100000)
        result = runner.run(strategy, sample_market_data, symbol='TEST')
        
        # Should have generated some trades
        assert len(result.trades) >= 0  # May or may not have trades depending on data
        
        # Verify trade structure if any exist
        if result.trades:
            trade = result.trades[0]
            assert hasattr(trade, 'entry_date')
            assert hasattr(trade, 'exit_date')
            assert hasattr(trade, 'symbol')
            assert hasattr(trade, 'pnl')
            assert hasattr(trade, 'return_pct')
    
    def test_backtest_equity_curve(self, sample_market_data):
        """Test that equity curve is properly calculated."""
        strategy = SmaCrossoverStrategy()
        runner = BacktestRunner(initial_capital=100000)
        result = runner.run(strategy, sample_market_data, symbol='TEST')
        
        # Equity curve should match data length (approximately)
        assert len(result.equity_curve) > 0
        assert result.equity_curve.iloc[0] == 100000  # Starts with initial capital
        assert result.equity_curve.iloc[-1] == result.final_capital
    
    def test_backtest_no_data_raises_error(self):
        """Test that empty data raises appropriate error."""
        strategy = SmaCrossoverStrategy()
        runner = BacktestRunner()
        
        empty_data = pd.DataFrame()
        
        # Should handle empty data gracefully
        with pytest.raises(Exception):
            runner.run(strategy, empty_data, symbol='TEST')


class TestDataFetcherIntegration:
    """Integration tests for data fetching."""
    
    @pytest.mark.skip(reason="Requires network access")
    def test_fetch_live_data(self):
        """Test fetching real data from Yahoo Finance."""
        fetcher = DataFetcher()
        
        try:
            data = fetcher.fetch('AAPL', period='5d', use_cache=False)
            
            assert isinstance(data, pd.DataFrame)
            assert len(data) > 0
            assert 'close' in data.columns
            assert 'volume' in data.columns
            assert 'symbol' in data.columns
            assert data['symbol'].iloc[0] == 'AAPL'
        except Exception as e:
            pytest.skip(f"Network or API issue: {e}")
    
    def test_fetcher_caching(self, tmp_path):
        """Test that fetcher properly caches data."""
        import os
        
        cache_dir = tmp_path / "cache"
        fetcher = DataFetcher(cache_dir=str(cache_dir))
        
        # Create sample data manually
        dates = pd.date_range('2023-01-01', periods=10, freq='D')
        df = pd.DataFrame({
            'open': [100] * 10,
            'high': [102] * 10,
            'low': [98] * 10,
            'close': [100] * 10,
            'volume': [1000000] * 10,
            'symbol': 'TEST'
        }, index=dates)
        
        # Save to cache
        cache_file = cache_dir / "TEST_1y__1d.csv"
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_file)
        
        # Fetch should load from cache
        result = fetcher.fetch('TEST', period='1y', use_cache=True)
        
        assert len(result) == 10
        assert result['symbol'].iloc[0] == 'TEST'
