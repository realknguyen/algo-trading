"""Risk Management System for trading."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta

from adapters.base_adapter import Order, OrderSide, Position
from logging.log_config import TradingLogger


class RiskLevel(Enum):
    """Risk level enumeration."""
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()


@dataclass
class RiskLimits:
    """Risk limits configuration."""
    # Position limits
    max_position_size_pct: float = 0.10  # Max 10% of portfolio per position
    max_total_exposure_pct: float = 1.0  # Max 100% total exposure
    max_open_positions: int = 5
    max_leverage: float = 1.0  # No leverage by default
    
    # Drawdown limits
    max_drawdown_pct: float = 10.0  # Stop trading at 10% drawdown
    daily_loss_limit: float = 1000.0  # Max daily loss in currency
    weekly_loss_limit: float = 5000.0  # Max weekly loss in currency
    
    # Per-trade limits
    max_risk_per_trade_pct: float = 1.0  # Risk 1% per trade
    stop_loss_default_pct: float = 2.0
    take_profit_default_pct: float = 4.0
    
    # Volatility limits
    max_position_volatility_pct: float = 5.0  # Don't trade if vol > 5%
    
    # Correlation limits
    max_correlation_exposure: int = 3  # Max correlated positions


@dataclass
class PositionRisk:
    """Risk metrics for a position."""
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    
    # Calculated metrics
    position_value: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl_pct: float = 0.0
    risk_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    risk_pct: float = 0.0
    
    def calculate(self) -> None:
        """Calculate risk metrics."""
        self.position_value = self.quantity * self.current_price
        self.unrealized_pnl = (self.current_price - self.entry_price) * self.quantity
        self.unrealized_pnl_pct = float(
            (self.current_price - self.entry_price) / self.entry_price * 100
        ) if self.entry_price > 0 else 0.0
        
        if self.stop_loss_price:
            self.risk_amount = abs(self.entry_price - self.stop_loss_price) * self.quantity
            self.risk_pct = float(self.risk_amount / (self.entry_price * self.quantity) * 100)


@dataclass
class PortfolioRisk:
    """Portfolio-level risk metrics."""
    total_capital: Decimal
    current_equity: Decimal
    
    # Exposure
    total_exposure: Decimal = field(default_factory=lambda: Decimal("0"))
    exposure_pct: float = 0.0
    open_positions: int = 0
    
    # P&L
    total_unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    daily_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    daily_pnl_pct: float = 0.0
    
    # Drawdown
    peak_equity: Decimal = field(default_factory=lambda: Decimal("0"))
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    
    # Risk level
    risk_level: RiskLevel = RiskLevel.LOW
    
    def calculate(self) -> None:
        """Calculate portfolio risk metrics."""
        self.exposure_pct = float(self.total_exposure / self.current_equity * 100) if self.current_equity > 0 else 0.0
        self.daily_pnl_pct = float(self.daily_pnl / self.total_capital * 100) if self.total_capital > 0 else 0.0
        
        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        
        # Calculate drawdown
        if self.peak_equity > 0:
            self.current_drawdown_pct = float(
                (self.peak_equity - self.current_equity) / self.peak_equity * 100
            )
            self.max_drawdown_pct = max(self.max_drawdown_pct, self.current_drawdown_pct)
        
        # Determine risk level
        if self.current_drawdown_pct > 10:
            self.risk_level = RiskLevel.CRITICAL
        elif self.current_drawdown_pct > 5:
            self.risk_level = RiskLevel.HIGH
        elif self.current_drawdown_pct > 2:
            self.risk_level = RiskLevel.MEDIUM
        else:
            self.risk_level = RiskLevel.LOW


class RiskManager:
    """Risk Manager for trading operations.
    
    Provides:
    - Position sizing based on risk parameters
    - Drawdown monitoring and circuit breakers
    - Pre-trade risk checks
    - Portfolio exposure tracking
    - Stop-loss and take-profit calculations
    """
    
    def __init__(
        self,
        limits: RiskLimits,
        initial_capital: Decimal
    ):
        self.limits = limits
        self.initial_capital = initial_capital
        self.logger = TradingLogger("RiskManager")
        
        # State tracking
        self.current_equity = initial_capital
        self.peak_equity = initial_capital
        self.daily_pnl = Decimal("0")
        self.weekly_pnl = Decimal("0")
        
        # Position tracking
        self.positions: Dict[str, PositionRisk] = {}
        self.position_history: List[Dict[str, Any]] = []
        
        # Daily tracking
        self.last_reset_date = datetime.now().date()
        self.trading_enabled = True
        
        # Circuit breaker callback
        self.on_circuit_breaker: Optional[Callable[[str], None]] = None
    
    def update_equity(self, new_equity: Decimal) -> None:
        """Update current equity and track metrics.
        
        Args:
            new_equity: New equity value
        """
        old_equity = self.current_equity
        self.current_equity = new_equity
        
        # Update peak equity
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
        
        # Calculate P&L
        pnl = new_equity - old_equity
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        
        # Check for daily reset
        current_date = datetime.now().date()
        if current_date != self.last_reset_date:
            if current_date - self.last_reset_date >= timedelta(days=7):
                self.weekly_pnl = Decimal("0")
            self.daily_pnl = Decimal("0")
            self.last_reset_date = current_date
        
        # Check circuit breakers
        self._check_circuit_breakers()
    
    def _check_circuit_breakers(self) -> None:
        """Check and trigger circuit breakers if limits breached."""
        portfolio_risk = self.get_portfolio_risk()
        
        # Check drawdown
        if portfolio_risk.current_drawdown_pct > self.limits.max_drawdown_pct:
            self._trigger_circuit_breaker(
                f"Max drawdown breached: {portfolio_risk.current_drawdown_pct:.2f}%"
            )
            return
        
        # Check daily loss
        if self.daily_pnl < -Decimal(str(self.limits.daily_loss_limit)):
            self._trigger_circuit_breaker(
                f"Daily loss limit breached: ${abs(self.daily_pnl):.2f}"
            )
            return
        
        # Check weekly loss
        if self.weekly_pnl < -Decimal(str(self.limits.weekly_loss_limit)):
            self._trigger_circuit_breaker(
                f"Weekly loss limit breached: ${abs(self.weekly_pnl):.2f}"
            )
            return
    
    def _trigger_circuit_breaker(self, reason: str) -> None:
        """Trigger circuit breaker to halt trading.
        
        Args:
            reason: Reason for triggering
        """
        self.trading_enabled = False
        self.logger.risk_event("CIRCUIT_BREAKER", reason)
        
        if self.on_circuit_breaker:
            try:
                self.on_circuit_breaker(reason)
            except Exception as e:
                self.logger.error("circuit_breaker", f"Callback error: {e}")
    
    def reset_circuit_breaker(self) -> None:
        """Reset circuit breaker and re-enable trading."""
        self.trading_enabled = True
        self.daily_pnl = Decimal("0")
        self.logger.logger.info("Circuit breaker reset, trading re-enabled")
    
    def can_trade(self) -> bool:
        """Check if trading is currently allowed."""
        return self.trading_enabled
    
    def check_trade_risk(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        stop_loss_price: Optional[Decimal] = None
    ) -> tuple[bool, Optional[str]]:
        """Check if a proposed trade passes risk checks.
        
        Args:
            symbol: Trading symbol
            side: Buy or sell
            quantity: Order quantity
            price: Order price
            stop_loss_price: Optional stop-loss price
            
        Returns:
            Tuple of (allowed, reason_if_not)
        """
        if not self.trading_enabled:
            return False, "Trading disabled by circuit breaker"
        
        # Check position size
        position_value = quantity * price
        position_pct = float(position_value / self.current_equity)
        
        if position_pct > self.limits.max_position_size_pct:
            return False, f"Position size {position_pct:.2%} exceeds max {self.limits.max_position_size_pct:.2%}"
        
        # Check total exposure
        total_exposure = self._calculate_total_exposure()
        new_exposure = total_exposure + position_value
        exposure_pct = float(new_exposure / self.current_equity)
        
        if exposure_pct > self.limits.max_total_exposure_pct:
            return False, f"Total exposure {exposure_pct:.2%} exceeds max {self.limits.max_total_exposure_pct:.2%}"
        
        # Check number of positions
        current_positions = len(self.positions)
        if symbol not in self.positions and current_positions >= self.limits.max_open_positions:
            return False, f"Max open positions ({self.limits.max_open_positions}) reached"
        
        # Check risk per trade
        if stop_loss_price:
            risk_amount = abs(price - stop_loss_price) * quantity
            risk_pct = float(risk_amount / self.current_equity * 100)
            
            if risk_pct > self.limits.max_risk_per_trade_pct:
                return False, f"Risk per trade {risk_pct:.2f}% exceeds max {self.limits.max_risk_per_trade_pct:.2f}%"
        
        return True, None
    
    def calculate_position_size(
        self,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        risk_pct: Optional[float] = None
    ) -> Decimal:
        """Calculate position size based on risk parameters.
        
        Args:
            entry_price: Planned entry price
            stop_loss_price: Stop-loss price
            risk_pct: Risk percentage (uses default if None)
            
        Returns:
            Calculated position size
        """
        risk_pct = risk_pct or self.limits.max_risk_per_trade_pct
        risk_amount = self.current_equity * Decimal(str(risk_pct)) / 100
        
        price_risk = abs(entry_price - stop_loss_price)
        if price_risk == 0:
            return Decimal("0")
        
        quantity = risk_amount / price_risk
        
        # Check against max position size
        max_position_value = self.current_equity * Decimal(str(self.limits.max_position_size_pct))
        max_quantity = max_position_value / entry_price
        
        return min(quantity, max_quantity)
    
    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        side: OrderSide,
        stop_loss_pct: Optional[float] = None
    ) -> Decimal:
        """Calculate stop-loss price.
        
        Args:
            entry_price: Entry price
            side: Buy or sell
            stop_loss_pct: Stop-loss percentage (uses default if None)
            
        Returns:
            Stop-loss price
        """
        stop_pct = stop_loss_pct or self.limits.stop_loss_default_pct
        
        if side == OrderSide.BUY:
            return entry_price * (1 - Decimal(str(stop_pct)) / 100)
        else:
            return entry_price * (1 + Decimal(str(stop_pct)) / 100)
    
    def calculate_take_profit(
        self,
        entry_price: Decimal,
        side: OrderSide,
        take_profit_pct: Optional[float] = None
    ) -> Decimal:
        """Calculate take-profit price.
        
        Args:
            entry_price: Entry price
            side: Buy or sell
            take_profit_pct: Take-profit percentage (uses default if None)
            
        Returns:
            Take-profit price
        """
        tp_pct = take_profit_pct or self.limits.take_profit_default_pct
        
        if side == OrderSide.BUY:
            return entry_price * (1 + Decimal(str(tp_pct)) / 100)
        else:
            return entry_price * (1 - Decimal(str(tp_pct)) / 100)
    
    def update_position(
        self,
        symbol: str,
        quantity: Decimal,
        entry_price: Decimal,
        current_price: Decimal,
        stop_loss_price: Optional[Decimal] = None,
        take_profit_price: Optional[Decimal] = None
    ) -> None:
        """Update or add position tracking.
        
        Args:
            symbol: Trading symbol
            quantity: Position quantity
            entry_price: Average entry price
            current_price: Current market price
            stop_loss_price: Stop-loss price
            take_profit_price: Take-profit price
        """
        position = PositionRisk(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            current_price=current_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price
        )
        position.calculate()
        
        self.positions[symbol] = position
    
    def remove_position(self, symbol: str) -> None:
        """Remove position from tracking.
        
        Args:
            symbol: Trading symbol
        """
        if symbol in self.positions:
            del self.positions[symbol]
    
    def get_position_risk(self, symbol: str) -> Optional[PositionRisk]:
        """Get risk metrics for a position.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            PositionRisk if position exists
        """
        return self.positions.get(symbol)
    
    def get_portfolio_risk(self) -> PortfolioRisk:
        """Get portfolio-level risk metrics.
        
        Returns:
            PortfolioRisk with calculated metrics
        """
        total_exposure = sum(p.position_value for p in self.positions.values())
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        
        portfolio = PortfolioRisk(
            total_capital=self.initial_capital,
            current_equity=self.current_equity,
            total_exposure=total_exposure,
            open_positions=len(self.positions),
            total_unrealized_pnl=total_unrealized,
            daily_pnl=self.daily_pnl,
            peak_equity=self.peak_equity,
            max_drawdown_pct=self._calculate_max_drawdown()
        )
        portfolio.calculate()
        
        return portfolio
    
    def _calculate_total_exposure(self) -> Decimal:
        """Calculate total portfolio exposure."""
        return sum(p.position_value for p in self.positions.values())
    
    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum historical drawdown."""
        if self.peak_equity > 0:
            return float((self.peak_equity - self.current_equity) / self.peak_equity * 100)
        return 0.0
    
    def get_risk_report(self) -> Dict[str, Any]:
        """Generate comprehensive risk report.
        
        Returns:
            Dictionary with risk metrics
        """
        portfolio = self.get_portfolio_risk()
        
        return {
            'trading_enabled': self.trading_enabled,
            'risk_level': portfolio.risk_level.name,
            'equity': {
                'initial': float(self.initial_capital),
                'current': float(self.current_equity),
                'peak': float(self.peak_equity)
            },
            'drawdown': {
                'current_pct': portfolio.current_drawdown_pct,
                'max_pct': portfolio.max_drawdown_pct,
                'limit_pct': self.limits.max_drawdown_pct
            },
            'pnl': {
                'daily': float(self.daily_pnl),
                'weekly': float(self.weekly_pnl),
                'unrealized': float(portfolio.total_unrealized_pnl)
            },
            'exposure': {
                'total': float(portfolio.total_exposure),
                'pct': portfolio.exposure_pct,
                'limit_pct': self.limits.max_total_exposure_pct * 100,
                'open_positions': portfolio.open_positions,
                'max_positions': self.limits.max_open_positions
            },
            'positions': [
                {
                    'symbol': p.symbol,
                    'quantity': float(p.quantity),
                    'value': float(p.position_value),
                    'unrealized_pnl_pct': p.unrealized_pnl_pct,
                    'risk_pct': p.risk_pct
                }
                for p in self.positions.values()
            ]
        }
