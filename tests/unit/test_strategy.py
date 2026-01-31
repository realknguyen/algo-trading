"""Tests for base strategy interface."""

import pytest
import pandas as pd
import numpy as np
from abc import ABC

from src.strategy import BaseStrategy, Signal


class TestBaseStrategy:
    """Test cases for BaseStrategy abstract class."""
    
    def test_base_strategy_is_abstract(self):
        """Test that BaseStrategy cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseStrategy(name="Test")
    
    def test_concrete_strategy_can_be_created(self):
        """Test that a concrete implementation can be created."""
        
        class ConcreteStrategy(BaseStrategy):
            def initialize(self, data):
                pass
            
            def on_data(self, data):
                return None
            
            def get_parameters(self):
                return self.params
        
        strategy = ConcreteStrategy(name="Test", params={'test': 1})
        assert strategy.name == "Test"
        assert strategy.params == {'test': 1}
        assert strategy._initialized is False


class TestSignal:
    """Test cases for Signal dataclass."""
    
    def test_signal_creation(self):
        """Test creating a Signal with all fields."""
        signal = Signal(
            symbol="AAPL",
            action="buy",
            timestamp=pd.Timestamp("2023-01-01"),
            price=150.0,
            confidence=0.8,
            metadata={'sma_20': 145.0}
        )
        
        assert signal.symbol == "AAPL"
        assert signal.action == "buy"
        assert signal.price == 150.0
        assert signal.confidence == 0.8
        assert signal.metadata == {'sma_20': 145.0}
    
    def test_signal_defaults(self):
        """Test Signal with default values."""
        signal = Signal(
            symbol="MSFT",
            action="sell",
            timestamp=pd.Timestamp("2023-01-01"),
            price=300.0
        )
        
        assert signal.confidence == 1.0  # Default
        assert signal.metadata is None  # Default
