"""Risk-aware SMA crossover strategy for the lightweight `src` stack."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from . import BaseStrategy, Signal


class SmaCrossoverRiskStrategy(BaseStrategy):
    """SMA crossover strategy with simple volatility and stop metadata."""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        defaults = {
            "fast_period": 10,
            "slow_period": 30,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 6.0,
            "risk_per_trade": 0.02,
            "volatility_filter": 5.0,
        }
        if params:
            defaults.update(params)
        super().__init__(name="SMA_Crossover_Risk", params=defaults)
        self._prev_crossover_state: Optional[bool] = None
        self._position = 0
        self._entry_price: Optional[float] = None

    def initialize(self, data: pd.DataFrame) -> None:
        indicators = self._with_indicators(data.copy())
        if len(indicators) >= 2:
            current = indicators.iloc[-1]
            if pd.notna(current["sma_fast"]) and pd.notna(current["sma_slow"]):
                self._prev_crossover_state = bool(current["sma_fast"] > current["sma_slow"])
        self._initialized = True

    def on_data(self, data: pd.DataFrame) -> Optional[Signal]:
        if len(data) < self.params["slow_period"]:
            return None

        indicators = self._with_indicators(data.copy())
        current = indicators.iloc[-1]
        previous = indicators.iloc[-2]

        required = ["sma_fast", "sma_slow", "atr", "volatility_pct"]
        if any(pd.isna(current[column]) for column in required):
            return None
        if pd.isna(previous["sma_fast"]) or pd.isna(previous["sma_slow"]):
            return None

        if float(current["volatility_pct"]) > float(self.params["volatility_filter"]):
            return None

        current_state = bool(current["sma_fast"] > current["sma_slow"])
        previous_state = bool(previous["sma_fast"] > previous["sma_slow"])
        self._prev_crossover_state = current_state

        if current_state == previous_state:
            return None

        action = "buy" if current_state else "sell"
        close_price = float(current["close"])
        stop_loss_pct = float(self.params["stop_loss_pct"]) / 100.0
        take_profit_pct = float(self.params["take_profit_pct"]) / 100.0

        if action == "buy":
            stop_loss_price = close_price * (1 - stop_loss_pct)
            take_profit_price = close_price * (1 + take_profit_pct)
            self._position = 1
            self._entry_price = close_price
        else:
            stop_loss_price = close_price * (1 + stop_loss_pct)
            take_profit_price = close_price * (1 - take_profit_pct)
            self._position = 0
            self._entry_price = None

        confidence = abs(float(current["sma_fast"]) - float(current["sma_slow"])) / float(
            current["sma_slow"]
        )
        confidence = max(0.0, min(confidence * 10, 1.0))

        return Signal(
            symbol=current.get("symbol", "UNKNOWN"),
            action=action,
            timestamp=current.name if isinstance(current.name, pd.Timestamp) else pd.Timestamp.now(),
            price=close_price,
            confidence=confidence,
            metadata={
                "sma_fast": float(current["sma_fast"]),
                "sma_slow": float(current["sma_slow"]),
                "atr": float(current["atr"]),
                "volatility_pct": float(current["volatility_pct"]),
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "risk_per_trade": self.params["risk_per_trade"],
            },
        )

    def get_parameters(self) -> Dict[str, Any]:
        return self.params.copy()

    def get_state(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "prev_crossover_state": self._prev_crossover_state,
            "position": self._position,
            "entry_price": self._entry_price,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self._initialized = bool(state.get("initialized", False))
        self._prev_crossover_state = state.get("prev_crossover_state")
        self._position = int(state.get("position", 0))
        self._entry_price = state.get("entry_price")

    def _with_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        fast = int(self.params["fast_period"])
        slow = int(self.params["slow_period"])

        data["sma_fast"] = data["close"].rolling(window=fast).mean()
        data["sma_slow"] = data["close"].rolling(window=slow).mean()

        prev_close = data["close"].shift(1)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - prev_close).abs(),
                (data["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        data["atr"] = true_range.rolling(window=fast).mean()
        data["volatility_pct"] = (data["atr"] / data["close"]) * 100
        return data
