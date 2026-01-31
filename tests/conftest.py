"""pytest configuration and shared fixtures."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import Mock


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for testing."""
    np.random.seed(42)
    dates = pd.date_range('2023-01-01', periods=100, freq='D')
    
    # Generate realistic price movement
    returns = np.random.randn(100) * 0.02
    prices = 100 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        'open': prices * (1 + np.random.randn(100) * 0.001),
        'high': prices * (1 + abs(np.random.randn(100)) * 0.01),
        'low': prices * (1 - abs(np.random.randn(100)) * 0.01),
        'close': prices,
        'volume': np.random.randint(1000000, 10000000, 100),
        'symbol': 'TEST'
    }, index=dates)
    
    return df


@pytest.fixture
def trending_data():
    """Generate trending data for crossover testing."""
    dates = pd.date_range('2023-01-01', periods=200, freq='D')
    
    # Start downtrend, then uptrend
    prices = []
    for i in range(200):
        if i < 100:
            prices.append(100 - i * 0.1 + np.random.randn() * 0.5)
        else:
            prices.append(90 + (i - 100) * 0.15 + np.random.randn() * 0.5)
    
    df = pd.DataFrame({
        'open': [p * 0.99 for p in prices],
        'high': [p * 1.02 for p in prices],
        'low': [p * 0.98 for p in prices],
        'close': prices,
        'volume': np.random.randint(1000000, 10000000, 200),
        'symbol': 'TEST'
    }, index=dates)
    
    return df


@pytest.fixture
def risk_limits():
    """Default risk limits for testing."""
    from src.risk.manager import RiskLimits
    
    return RiskLimits(
        max_position_size=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit=1000.0,
        max_open_positions=5
    )


@pytest.fixture
def mock_broker():
    """Mock broker for testing."""
    broker = Mock()
    broker.submit_order.return_value = {
        'id': 'test-order-123',
        'status': 'filled',
        'filled_qty': 100,
        'avg_price': 150.0
    }
    broker.get_account.return_value = {
        'cash': 100000.0,
        'buying_power': 200000.0
    }
    broker.get_positions.return_value = []
    broker.cancel_order.return_value = True
    broker.get_order.return_value = {
        'id': 'test-order-123',
        'status': 'filled',
        'filled_qty': 100,
        'avg_price': 150.0
    }
    
    return broker


@pytest.fixture
def temp_database(tmp_path):
    """Create temporary SQLite database for testing."""
    database_url = f"sqlite:///{tmp_path}/test.db"
    
    yield database_url


@pytest.fixture
def sample_order():
    """Create a sample order for testing."""
    from src.broker import Order, OrderSide, OrderType
    
    return Order(
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.MARKET
    )


@pytest.fixture
def execution_engine(mock_broker):
    """Create an execution engine with mocked broker."""
    from src.execution.engine import ExecutionEngine
    
    return ExecutionEngine(broker=mock_broker)


@pytest.fixture
def risk_manager(risk_limits):
    """Create a risk manager with default limits."""
    from src.risk.manager import RiskManager
    
    return RiskManager(limits=risk_limits, initial_capital=100000.0)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for async tests."""
    import asyncio
    
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
