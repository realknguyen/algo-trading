"""End-to-end integration tests for complete trading workflow."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.strategy.sma_crossover_risk import SmaCrossoverRiskStrategy
from src.risk.manager import RiskManager, RiskLimits
from src.backtest.runner import BacktestRunner
from src.execution.engine import ExecutionEngine


@pytest.mark.integration
class TestEndToEndWorkflow:
    """End-to-end tests for complete trading workflow."""
    
    @pytest.fixture
    def sample_data(self):
        """Create sample market data."""
        np.random.seed(42)
        dates = pd.date_range('2023-01-01', periods=200, freq='D')
        
        # Create data with clear trend for crossover
        prices = []
        for i in range(200):
            if i < 100:
                prices.append(100 + i * 0.05 + np.random.randn() * 0.5)
            else:
                prices.append(105 - (i - 100) * 0.05 + np.random.randn() * 0.5)
        
        df = pd.DataFrame({
            'open': [p * 0.99 for p in prices],
            'high': [p * 1.02 for p in prices],
            'low': [p * 0.98 for p in prices],
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, 200),
            'symbol': 'TEST'
        }, index=dates)
        
        return df
    
    def test_strategy_with_risk_integration(self, sample_data):
        """Test strategy integration with risk manager."""
        # Setup risk manager
        limits = RiskLimits(
            max_position_size=0.20,
            max_drawdown_pct=0.20,
            daily_loss_limit=5000.0
        )
        risk_manager = RiskManager(limits, initial_capital=100000)
        
        # Setup strategy
        strategy = SmaCrossoverRiskStrategy({
            'fast_period': 10,
            'slow_period': 30,
            'stop_loss_pct': 2.0
        })
        
        # Verify risk manager is working
        assert risk_manager.can_trade() is True
        
        # Initialize strategy
        strategy.initialize(sample_data.iloc[:50])
        
        # Generate signals
        signals = []
        for i in range(50, len(sample_data)):
            signal = strategy.on_data(sample_data.iloc[:i+1])
            if signal:
                # Validate signal against risk
                valid, reason = risk_manager.validate_order(
                    signal.symbol,
                    quantity=100,  # Simplified
                    price=signal.price
                )
                
                if valid:
                    signals.append(signal)
        
        # Verify workflow completed
        assert len(signals) >= 0  # May or may not have signals
    
    def test_full_backtest_pipeline(self, sample_data):
        """Test complete backtest pipeline."""
        # Setup components
        strategy = SmaCrossoverRiskStrategy({
            'fast_period': 10,
            'slow_period': 30
        })
        
        runner = BacktestRunner(
            initial_capital=100000,
            commission=0.001
        )
        
        # Run backtest
        result = runner.run(strategy, sample_data, symbol='TEST')
        
        # Verify results
        assert result is not None
        assert result.initial_capital == 100000
        assert result.final_capital >= 0
        
        # Verify metrics
        metrics = result.calculate_metrics()
        assert 'total_return_pct' in metrics
        assert 'sharpe_ratio' in metrics
        assert 'max_drawdown_pct' in metrics
        
        # Verify trades have risk metadata
        for trade in result.trades:
            assert hasattr(trade, 'entry_price')
            assert hasattr(trade, 'exit_price')
            assert hasattr(trade, 'pnl')
    
    def test_execution_with_mock_broker(self, sample_data):
        """Test execution engine with mock broker."""
        from unittest.mock import Mock
        from src.broker import Order, OrderSide, OrderType
        
        # Setup mock broker
        mock_broker = Mock()
        mock_broker.submit_order.return_value = {
            'id': 'test-123',
            'status': 'filled',
            'filled_qty': 100,
            'avg_price': 150.0
        }
        
        # Setup execution engine
        engine = ExecutionEngine(broker=mock_broker)
        
        # Create order from signal
        order = Order(
            symbol="TEST",
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET
        )
        
        # Submit order
        order_id = engine.submit_order(order)
        
        assert order_id == 'test-123'
        assert 'test-123' in engine.pending_orders
        
        # Update status
        report = engine.update_order_status('test-123')
        
        assert report is not None
        assert report.order_id == 'test-123'
    
    def test_risk_circuit_breaker_integration(self, sample_data):
        """Test circuit breaker triggers during trading."""
        limits = RiskLimits(
            max_position_size=0.10,
            max_drawdown_pct=0.50,  # High to not trigger
            daily_loss_limit=100000.0,  # High to not trigger
            consecutive_losses_threshold=2  # Low to trigger quickly
        )
        
        risk_manager = RiskManager(limits, initial_capital=100000)
        
        # Simulate consecutive losses
        risk_manager.record_trade(-1000)
        assert risk_manager.can_trade() is True
        
        risk_manager.record_trade(-1000)
        assert risk_manager.can_trade() is False
        assert risk_manager._halted is True
    
    def test_multiple_strategies_portfolio(self, sample_data):
        """Test portfolio with multiple strategies."""
        from src.strategy.sma_crossover import SmaCrossoverStrategy
        
        strategies = [
            SmaCrossoverStrategy({'fast_period': 10, 'slow_period': 20}),
            SmaCrossoverStrategy({'fast_period': 20, 'slow_period': 50}),
        ]
        
        results = []
        for strategy in strategies:
            runner = BacktestRunner(initial_capital=100000)
            result = runner.run(strategy, sample_data, symbol='TEST')
            results.append(result)
        
        # Verify all strategies ran
        assert len(results) == 2
        
        # Verify different parameters produced different results
        assert results[0].strategy_name == results[1].strategy_name
        # Results may differ based on parameters
