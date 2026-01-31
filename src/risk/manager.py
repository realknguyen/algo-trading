"""Risk management module."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskLimits:
    """Risk limits configuration."""
    max_position_size: float = 0.1  # Max 10% of portfolio per position
    max_drawdown_pct: float = 10.0  # Stop trading at 10% drawdown
    daily_loss_limit: float = 1000.0  # Max daily loss in currency
    max_open_positions: int = 5  # Max number of open positions
    max_leverage: float = 1.0  # No leverage by default


class RiskManager:
    """Manage trading risk and position sizing."""
    
    def __init__(self, limits: RiskLimits, initial_capital: float):
        self.limits = limits
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.peak_capital = initial_capital
        self.daily_pnl = 0.0
        self.open_positions = 0
    
    def can_trade(self) -> bool:
        """Check if trading is allowed based on risk limits."""
        # Check drawdown
        drawdown_pct = (self.peak_capital - self.current_capital) / self.peak_capital * 100
        if drawdown_pct > self.limits.max_drawdown_pct:
            return False
        
        # Check daily loss limit
        if self.daily_pnl < -self.limits.daily_loss_limit:
            return False
        
        return True
    
    def calculate_position_size(self, price: float, confidence: float = 1.0) -> float:
        """Calculate position size based on risk limits."""
        max_value = self.current_capital * self.limits.max_position_size * confidence
        quantity = max_value / price
        return quantity
    
    def update_capital(self, new_capital: float) -> None:
        """Update current capital and track peak."""
        self.current_capital = new_capital
        if new_capital > self.peak_capital:
            self.peak_capital = new_capital
    
    def get_risk_metrics(self) -> dict:
        """Get current risk metrics."""
        drawdown_pct = (self.peak_capital - self.current_capital) / self.peak_capital * 100
        return {
            'current_capital': self.current_capital,
            'peak_capital': self.peak_capital,
            'drawdown_pct': drawdown_pct,
            'daily_pnl': self.daily_pnl,
            'open_positions': self.open_positions,
            'can_trade': self.can_trade()
        }
