"""Lightweight risk management primitives used by the `src` test harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict


@dataclass
class RiskLimits:
    """Risk limits configuration for the lightweight `src` stack."""

    max_position_size: float = 0.10
    max_drawdown_pct: float = 0.10
    daily_loss_limit: float = 1000.0
    max_open_positions: int = 10
    max_risk_per_trade: float = 0.02
    max_asset_exposure: float = 0.15
    consecutive_losses_threshold: int = 3
    trading_start_time: time | None = None
    trading_end_time: time | None = None


class RiskManager:
    """Manage trading risk and simple circuit-breaker behaviour."""

    def __init__(self, limits: RiskLimits, initial_capital: float):
        self.limits = limits
        self.initial_capital = float(initial_capital)
        self.current_capital = float(initial_capital)
        self.peak_capital = float(initial_capital)
        self.daily_pnl = 0.0
        self.open_positions = 0
        self.positions: Dict[str, Dict[str, float]] = {}
        self._halted = False
        self._consecutive_losses = 0

    def _is_trading_time_allowed(self) -> bool:
        """Check if now is within the configured trading window."""
        if not self.limits.trading_start_time or not self.limits.trading_end_time:
            return True

        now = datetime.now().time()
        start = self.limits.trading_start_time
        end = self.limits.trading_end_time

        if start <= end:
            return start <= now <= end

        return now >= start or now <= end

    def can_trade(self) -> bool:
        """Check whether trading is currently permitted."""
        if self._halted:
            return False

        if self.open_positions >= self.limits.max_open_positions:
            return False

        drawdown = (
            (self.peak_capital - self.current_capital) / self.peak_capital
            if self.peak_capital
            else 0.0
        )
        if drawdown > self.limits.max_drawdown_pct:
            return False

        if self.daily_pnl <= -self.limits.daily_loss_limit:
            return False

        return self._is_trading_time_allowed()

    def calculate_position_size(
        self,
        price: float,
        confidence: float = 1.0,
        stop_loss_pct: float | None = None,
    ) -> float:
        """Calculate position size based on portfolio and risk budgets."""
        if price <= 0:
            return 0.0

        confidence = max(0.0, min(1.0, float(confidence)))
        max_notional = self.current_capital * self.limits.max_position_size * confidence
        max_by_position = max_notional / price

        if not stop_loss_pct or stop_loss_pct <= 0:
            return max_by_position

        risk_budget = self.current_capital * self.limits.max_risk_per_trade * confidence
        risk_per_unit = price * stop_loss_pct
        if risk_per_unit <= 0:
            return 0.0

        max_by_risk = risk_budget / risk_per_unit
        return min(max_by_position, max_by_risk)

    def validate_order(self, symbol: str, quantity: float, price: float) -> tuple[bool, str]:
        """Validate an order against current risk limits."""
        if quantity <= 0 or price <= 0:
            return False, "Quantity and price must be positive"

        if not self.can_trade():
            return False, "Trading not allowed by risk limits"

        order_value = quantity * price
        max_asset_value = self.current_capital * self.limits.max_asset_exposure
        existing_exposure = float(self.positions.get(symbol, {}).get("value", 0.0))
        if existing_exposure > 0 and existing_exposure + order_value > max_asset_value:
            return False, "Order would exceed symbol exposure limits"

        max_position_value = self.current_capital * self.limits.max_position_size
        if order_value > max_position_value:
            return (
                False,
                f"Order size exceeds max position limit of {self.limits.max_position_size:.0%}",
            )

        if existing_exposure + order_value > max_asset_value:
            return False, "Order would exceed symbol exposure limits"

        return True, "OK"

    def update_capital(self, new_capital: float) -> None:
        """Update current capital and track peak."""
        self.current_capital = float(new_capital)
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

    def record_trade(self, pnl: float) -> None:
        """Record realized PnL and update circuit-breaker state."""
        pnl = float(pnl)
        self.daily_pnl += pnl
        self.current_capital += pnl
        self.update_capital(self.current_capital)

        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.limits.consecutive_losses_threshold:
                self._halted = True
        else:
            self._consecutive_losses = 0

    def get_risk_metrics(self) -> Dict[str, Any]:
        """Return current risk metrics as plain scalars."""
        drawdown_amount = self.peak_capital - self.current_capital
        drawdown_pct = (
            (drawdown_amount / self.peak_capital) * 100 if self.peak_capital else 0.0
        )
        return {
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "drawdown_pct": drawdown_pct,
            "drawdown_amount": drawdown_amount,
            "daily_pnl": self.daily_pnl,
            "open_positions": self.open_positions,
            "can_trade": self.can_trade(),
            "consecutive_losses": self._consecutive_losses,
        }
