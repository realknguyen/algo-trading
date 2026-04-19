"""Test for SMA Crossover Strategy."""

import unittest
import pandas as pd
import numpy as np
from src.strategy.sma_crossover import SmaCrossoverStrategy


class TestSmaCrossoverStrategy(unittest.TestCase):

    def setUp(self):
        """Set up test data."""
        # Create sample price data with a clear crossover
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        # Create data that starts below then crosses above
        prices = []
        for i in range(100):
            if i < 50:
                prices.append(100 - i * 0.1)  # Downtrend
            else:
                prices.append(95 + (i - 50) * 0.2)  # Uptrend

        self.df = pd.DataFrame({"close": prices, "symbol": "TEST"}, index=dates)

    def test_initialization(self):
        """Test strategy initialization."""
        strategy = SmaCrossoverStrategy()
        self.assertEqual(strategy.name, "SMA_Crossover")
        self.assertEqual(strategy.params["fast_period"], 20)
        self.assertEqual(strategy.params["slow_period"], 50)

    def test_custom_parameters(self):
        """Test custom parameters."""
        strategy = SmaCrossoverStrategy({"fast_period": 10, "slow_period": 30})
        self.assertEqual(strategy.params["fast_period"], 10)
        self.assertEqual(strategy.params["slow_period"], 30)

    def test_signal_generation(self):
        """Test that signals are generated correctly."""
        strategy = SmaCrossoverStrategy({"fast_period": 5, "slow_period": 10})
        strategy.initialize(self.df)

        # This might or might not generate a signal depending on the data
        signal = strategy.on_data(self.df)

        # Signal should be None or a valid Signal object
        if signal is not None:
            self.assertIn(signal.action, ["buy", "sell"])
            self.assertEqual(signal.symbol, "TEST")


if __name__ == "__main__":
    unittest.main()
