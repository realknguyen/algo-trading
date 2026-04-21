"""Tests for base strategy interface."""

import pytest
import pandas as pd
import numpy as np
from abc import ABC

import src.strategy as strategy_module
from src.strategy import BaseStrategy, Signal, describe_strategies, describe_strategy


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

        strategy = ConcreteStrategy(name="Test", params={"test": 1})
        assert strategy.name == "Test"
        assert strategy.params == {"test": 1}
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
            metadata={"sma_20": 145.0},
        )

        assert signal.symbol == "AAPL"
        assert signal.action == "buy"
        assert signal.price == 150.0
        assert signal.confidence == 0.8
        assert signal.metadata == {"sma_20": 145.0}

    def test_signal_defaults(self):
        """Test Signal with default values."""
        signal = Signal(
            symbol="MSFT", action="sell", timestamp=pd.Timestamp("2023-01-01"), price=300.0
        )

        assert signal.confidence == 1.0  # Default
        assert signal.metadata is None  # Default


class TestStrategyDiscovery:
    """Tests for strategy registry discovery helpers."""

    def test_describe_strategy_returns_summary_and_defaults(self):
        """Known strategies should expose a short description and default params."""
        strategy = describe_strategy("sma_crossover_risk")

        assert strategy is not None
        assert strategy.name == "sma_crossover_risk"
        assert strategy.description == "SMA crossover strategy with simple volatility and stop metadata."
        assert strategy.default_params["fast_period"] == 10
        assert strategy.default_params["take_profit_pct"] == 6.0

    def test_describe_strategies_lists_registered_entries(self):
        """Strategy discovery should cover the whole registry."""
        strategies = describe_strategies()

        assert [strategy.name for strategy in strategies] == [
            "sma_crossover",
            "sma_crossover_risk",
        ]

    def test_describe_strategy_supports_registry_entries_that_require_params_arg(self, monkeypatch):
        """Discovery should use the same constructor contract as runtime creation."""

        class ParamsRequiredStrategy(BaseStrategy):
            """Strategy with required params constructor."""

            def __init__(self, params):
                super().__init__(name="ParamsRequired", params=params or {"window": 7})

            def initialize(self, data):
                pass

            def on_data(self, data):
                return None

            def get_parameters(self):
                return self.params.copy()

        monkeypatch.setitem(
            strategy_module._STRATEGY_REGISTRY,
            "params_required",
            ParamsRequiredStrategy,
        )

        strategy = describe_strategy("params_required")

        assert strategy is not None
        assert strategy.name == "params_required"
        assert strategy.description == "Strategy with required params constructor."
        assert strategy.default_params == {"window": 7}
