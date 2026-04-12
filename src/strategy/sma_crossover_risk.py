"""SMA crossover strategy with risk-aware signal metadata."""

from typing import Dict, Any, Optional
import math
import pandas as pd

from . import BaseStrategy, Signal


class SmaCrossoverRiskStrategy(BaseStrategy):
    """SMA crossover strategy that adds risk targets and state tracking."""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        default_params = {
            "fast_period": 10,
            "slow_period": 30,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 6.0,
            "risk_per_trade": 0.02,
            "volatility_filter": 5.0,
            "atr_period": 14,
        }
        if params:
            default_params.update(params)
        super().__init__(name="SMA_Crossover_Risk", params=default_params)

        self._prev_crossover_state: Optional[bool] = None
        self._position = 0
        self._entry_price: Optional[float] = None

    @staticmethod
    def _safe_numeric(value: float | None, fallback: float = 0.0) -> float:
        if value is None or pd.isna(value) or math.isnan(value):
            return fallback
        return float(value)

    def initialize(self, data: pd.DataFrame) -> None:
        if data.empty:
            return

        fast = self.params["fast_period"]
        slow = self.params["slow_period"]
        atr_period = max(1, int(self.params["atr_period"]))

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
        data["atr"] = true_range.rolling(window=atr_period).mean()
        data["volatility_pct"] = (data["atr"] / data["close"]) * 100

        if len(data) >= 2 and pd.notna(data.iloc[-1]["sma_fast"]) and pd.notna(data.iloc[-1]["sma_slow"]):
            self._prev_crossover_state = data.iloc[-1]["sma_fast"] > data.iloc[-1]["sma_slow"]

        self._initialized = True

    def on_data(self, data: pd.DataFrame) -> Optional[Signal]:
        if not self._initialized:
            self.initialize(data)

        if data.empty or len(data) < self.params["slow_period"]:
            return None

        fast = int(self.params["fast_period"])
        slow = int(self.params["slow_period"])
        atr_period = max(1, int(self.params["atr_period"]))
        volatility_filter = float(self.params["volatility_filter"])

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
        data["atr"] = true_range.rolling(window=atr_period).mean()
        data["volatility_pct"] = (data["atr"] / data["close"]) * 100

        current = data.iloc[-1]
        previous = data.iloc[-2] if len(data) >= 2 else None

        if pd.isna(current["sma_fast"]) or pd.isna(current["sma_slow"]):
            return None

        current_state = bool(current["sma_fast"] > current["sma_slow"])
        prev_state = (
            bool(previous["sma_fast"] > previous["sma_slow"])
            if previous is not None and pd.notna(previous["sma_fast"]) and pd.notna(previous["sma_slow"])
            else None
        )
        self._prev_crossover_state = current_state

        volatility_pct = self._safe_numeric(current.get("volatility_pct"), 0.0)
        if volatility_pct > volatility_filter:
            return None

        signal = None
        atr = self._safe_numeric(current.get("atr"), 0.0)
        confidence = self._safe_numeric(current.get("sma_fast") - current.get("sma_slow"), 0.0)
        slow_value = self._safe_numeric(current.get("sma_slow"), 1.0)
        if slow_value > 0:
            confidence = confidence / slow_value
        confidence = max(0.0, min(1.0, confidence))

        stop_loss_pct = float(self.params["stop_loss_pct"]) / 100.0
        take_profit_pct = float(self.params["take_profit_pct"]) / 100.0
        symbol = current.get("symbol", "UNKNOWN")
        price = float(current["close"])
        timestamp = current.name if isinstance(current.name, pd.Timestamp) else pd.Timestamp.now()

        if prev_state is not None and current_state != prev_state:
            if current_state:
                self._position = 1
                stop_loss_price = price * (1 - stop_loss_pct)
                take_profit_price = price * (1 + take_profit_pct)
                signal = Signal(
                    symbol=symbol,
                    action="buy",
                    timestamp=timestamp,
                    price=price,
                    confidence=confidence,
                    metadata={
                        "sma_fast": float(current["sma_fast"]),
                        "sma_slow": float(current["sma_slow"]),
                        "volatility_pct": volatility_pct,
                        "atr": atr,
                        "stop_loss_price": stop_loss_price,
                        "take_profit_price": take_profit_price,
                        "crossover": "bullish",
                    },
                )
                self._entry_price = price
            elif not current_state:
                stop_loss_price = price * (1 + stop_loss_pct)
                take_profit_price = price * (1 - take_profit_pct)
                signal = Signal(
                    symbol=symbol,
                    action="sell",
                    timestamp=timestamp,
                    price=price,
                    confidence=confidence,
                    metadata={
                        "sma_fast": float(current["sma_fast"]),
                        "sma_slow": float(current["sma_slow"]),
                        "volatility_pct": volatility_pct,
                        "atr": atr,
                        "stop_loss_price": stop_loss_price,
                        "take_profit_price": take_profit_price,
                        "crossover": "bearish",
                    },
                )
                self._position = 0
                self._entry_price = None

        return signal

    def get_parameters(self) -> Dict[str, Any]:
        return self.params.copy()

    def get_state(self) -> Dict[str, Any]:
        return {
            "_initialized": self._initialized,
            "_prev_crossover_state": self._prev_crossover_state,
            "_position": self._position,
            "_entry_price": self._entry_price,
            "params": self.params.copy(),
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self._initialized = bool(state.get("_initialized", False))
        self._prev_crossover_state = state.get("_prev_crossover_state")
        self._position = int(state.get("_position", 0))
        self._entry_price = state.get("_entry_price")
        self.params.update(state.get("params", {}))
