"""Backtesting engine for strategy evaluation."""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from enum import Enum

import pandas as pd
import numpy as np

from algorithms.base_algorithm import BaseAlgorithm, Signal
from adapters.base_adapter import Order, OrderSide, OrderStatus
from risk_management.risk_manager import RiskManager, RiskLimits
from log_config import TradingLogger


class BacktestStatus(Enum):
    """Backtest execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class Trade:
    """Represents a completed trade."""

    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: OrderSide
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    return_pct: float

    # Additional metrics
    max_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    holding_periods: int = 0


@dataclass
class BacktestResult:
    """Results from a backtest run."""

    algorithm_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: Decimal
    final_capital: Decimal

    # Returns
    total_return: Decimal = field(default_factory=lambda: Decimal("0"))
    total_return_pct: float = 0.0

    # Lists
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series())
    signals: List[Signal] = field(default_factory=list)

    # Performance metrics
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: Decimal = field(default_factory=lambda: Decimal("0"))
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0

    # Trade metrics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_trade_return: float = 0.0
    avg_winning_trade: float = 0.0
    avg_losing_trade: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    num_trades: int = 0
    num_winning_trades: int = 0
    num_losing_trades: int = 0

    # Risk metrics
    volatility: float = 0.0
    var_95: float = 0.0  # Value at Risk
    expected_shortfall: float = 0.0

    def calculate_metrics(self) -> Dict[str, Any]:
        """Calculate comprehensive performance metrics."""
        if not self.trades:
            return {}

        returns = [t.return_pct for t in self.trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]

        self.num_trades = len(returns)
        self.num_winning_trades = len(wins)
        self.num_losing_trades = len(losses)
        self.win_rate = len(wins) / len(returns) if returns else 0

        # P&L metrics
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        self.profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        self.avg_trade_return = np.mean(returns) if returns else 0
        self.avg_winning_trade = np.mean(wins) if wins else 0
        self.avg_losing_trade = np.mean(losses) if losses else 0
        self.largest_win = max(wins) if wins else 0
        self.largest_loss = min(losses) if losses else 0

        # Calculate max drawdown from equity curve
        if not self.equity_curve.empty:
            rolling_max = self.equity_curve.cummax()
            drawdown = (self.equity_curve - rolling_max) / rolling_max
            self.max_drawdown_pct = drawdown.min() * 100
            self.max_drawdown = drawdown.min() * float(self.initial_capital)

            # Calculate daily returns
            daily_returns = self.equity_curve.pct_change().dropna()

            if len(daily_returns) > 1:
                # Volatility (annualized)
                self.volatility = daily_returns.std() * np.sqrt(252) * 100

                # Sharpe ratio (assuming risk-free rate of 0)
                if daily_returns.std() > 0:
                    self.sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

                # Sortino ratio (downside deviation)
                downside_returns = daily_returns[daily_returns < 0]
                if len(downside_returns) > 0 and downside_returns.std() > 0:
                    self.sortino_ratio = (daily_returns.mean() / downside_returns.std()) * np.sqrt(
                        252
                    )

                # VaR 95%
                self.var_95 = np.percentile(daily_returns, 5) * 100

                # Expected Shortfall (CVaR)
                var_95_idx = daily_returns <= (self.var_95 / 100)
                if var_95_idx.any():
                    self.expected_shortfall = daily_returns[var_95_idx].mean() * 100

                # Calmar ratio
                if self.max_drawdown_pct != 0:
                    annual_return = (
                        (float(self.final_capital) / float(self.initial_capital))
                        ** (252 / len(daily_returns))
                        - 1
                    ) * 100
                    self.calmar_ratio = annual_return / abs(self.max_drawdown_pct)

        return {
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "calmar_ratio": self.calmar_ratio,
            "win_rate": self.win_rate * 100,
            "profit_factor": self.profit_factor,
            "num_trades": self.num_trades,
            "volatility": self.volatility,
            "var_95": self.var_95,
        }

    def summary(self) -> str:
        """Generate a formatted summary report."""
        metrics = self.calculate_metrics()

        report = f"""
{'='*60}
Backtest Results: {self.algorithm_name}
{'='*60}
Period: {self.start_date.date()} to {self.end_date.date()}
Initial Capital: ${float(self.initial_capital):,.2f}
Final Capital: ${float(self.final_capital):,.2f}
Total Return: {self.total_return_pct:.2f}%
{'-'*60}
Performance Metrics:
  Sharpe Ratio: {self.sharpe_ratio:.2f}
  Sortino Ratio: {self.sortino_ratio:.2f}
  Max Drawdown: {self.max_drawdown_pct:.2f}%
  Calmar Ratio: {self.calmar_ratio:.2f}
  Volatility: {self.volatility:.2f}%
  VaR (95%): {self.var_95:.2f}%
{'-'*60}
Trade Statistics:
  Total Trades: {self.num_trades}
  Win Rate: {self.win_rate*100:.1f}%
  Profit Factor: {self.profit_factor:.2f}
  Avg Return: {self.avg_trade_return:.2f}%
  Avg Win: {self.avg_winning_trade:.2f}%
  Avg Loss: {self.avg_losing_trade:.2f}%
  Largest Win: {self.largest_win:.2f}%
  Largest Loss: {self.largest_loss:.2f}%
{'='*60}
"""
        return report

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "algorithm_name": self.algorithm_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate": self.win_rate * 100,
            "num_trades": self.num_trades,
            "trades": [
                {
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "symbol": t.symbol,
                    "side": t.side.value,
                    "pnl": float(t.pnl),
                    "return_pct": t.return_pct,
                }
                for t in self.trades
            ],
        }


class BacktestEngine:
    """Backtesting engine for strategy evaluation.

    Simulates trading with historical data, accounting for:
    - Commission costs
    - Slippage
    - Risk management rules
    """

    def __init__(
        self, initial_capital: float = 100000.0, commission: float = 0.001, slippage: float = 0.0005
    ):
        self.initial_capital = Decimal(str(initial_capital))
        self.commission = commission
        self.slippage = slippage
        self.logger = TradingLogger("BacktestEngine")

        # State
        self.capital = self.initial_capital
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        self.signals: List[Signal] = []

        # Risk manager
        self.risk_manager = RiskManager(limits=RiskLimits(), initial_capital=self.initial_capital)

    async def run(
        self,
        algorithm: BaseAlgorithm,
        data: Dict[str, pd.DataFrame],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> BacktestResult:
        """
        Run a backtest for a given strategy and data.

        Args:
            algorithm: Strategy instance implementing BaseAlgorithm
            data: Dictionary of symbol -> OHLCV DataFrame
            progress_callback: Optional callback(progress, total)

        Returns:
            BacktestResult with performance metrics
        """
        self.logger.logger.info(f"Starting backtest for {algorithm.config.name}")

        # Reset state
        self.capital = self.initial_capital
        self.positions.clear()
        self.trades.clear()
        self.equity_curve.clear()
        self.signals.clear()

        # Get date range
        all_dates = set()
        for df in data.values():
            all_dates.update(df.index)
        sorted_dates = sorted(all_dates)

        if not sorted_dates:
            raise ValueError("No data provided for backtest")

        # Warmup period
        warmup_periods = algorithm.config.lookback_periods

        # Initialize algorithm with warmup data
        warmup_data = {}
        for symbol, df in data.items():
            if len(df) > warmup_periods:
                warmup_data[symbol] = df.iloc[:warmup_periods]
            else:
                warmup_data[symbol] = df

        algorithm.initialize(warmup_data)

        # Run simulation
        total_periods = len(sorted_dates) - warmup_periods

        for i, current_time in enumerate(sorted_dates[warmup_periods:]):
            # Report progress
            if progress_callback and i % 100 == 0:
                progress_callback(i, total_periods)

            # Build current data window
            current_data = {}
            for symbol, df in data.items():
                mask = df.index <= current_time
                current_data[symbol] = df[mask].copy()

            # Update positions with current prices
            self._update_positions(current_data)

            # Process data through algorithm
            signal = await algorithm.process_data(current_data)

            if signal:
                self.signals.append(signal)

                # Simulate execution
                await self._execute_signal(signal, current_data)

            # Record equity
            total_equity = self._calculate_equity(current_data)
            self.equity_curve.append(float(total_equity))

            # Update risk manager
            self.risk_manager.update_equity(total_equity)

        # Close any open positions at the end
        self._close_all_positions(sorted_dates[-1], data)

        # Create result
        result = BacktestResult(
            algorithm_name=algorithm.config.name,
            start_date=sorted_dates[warmup_periods],
            end_date=sorted_dates[-1],
            initial_capital=self.initial_capital,
            final_capital=self.capital,
            total_return=self.capital - self.initial_capital,
            total_return_pct=float(
                (self.capital - self.initial_capital) / self.initial_capital * 100
            ),
            trades=self.trades,
            equity_curve=pd.Series(self.equity_curve, index=sorted_dates[warmup_periods:]),
            signals=self.signals,
        )

        # Calculate metrics
        result.calculate_metrics()

        self.logger.logger.info(
            f"Backtest completed: {result.num_trades} trades, {result.total_return_pct:.2f}% return"
        )

        return result

    def _update_positions(self, data: Dict[str, pd.DataFrame]) -> None:
        """Update position P&L with current prices."""
        for symbol, position in self.positions.items():
            if symbol in data and not data[symbol].empty:
                current_price = Decimal(str(data[symbol]["close"].iloc[-1]))
                position["current_price"] = current_price

                # Calculate unrealized P&L
                if position["side"] == OrderSide.BUY:
                    position["unrealized_pnl"] = (
                        current_price - position["entry_price"]
                    ) * position["quantity"]
                else:
                    position["unrealized_pnl"] = (
                        position["entry_price"] - current_price
                    ) * position["quantity"]

    async def _execute_signal(self, signal: Signal, data: Dict[str, pd.DataFrame]) -> None:
        """Simulate execution of a signal."""
        if signal.action not in ["buy", "sell"]:
            return

        symbol = signal.symbol

        if symbol not in data or data[symbol].empty:
            return

        # Get execution price with slippage
        current_price = Decimal(str(data[symbol]["close"].iloc[-1]))

        if signal.action == "buy":
            execution_price = current_price * (1 + Decimal(str(self.slippage)))
            side = OrderSide.BUY
        else:
            execution_price = current_price * (1 - Decimal(str(self.slippage)))
            side = OrderSide.SELL

        # Calculate quantity
        if signal.quantity:
            quantity = signal.quantity
        else:
            # Use 10% of capital by default
            quantity = (self.capital * Decimal("0.1")) / execution_price

        # Apply commission
        commission_amount = execution_price * quantity * Decimal(str(self.commission))

        # Check if we have enough capital
        if side == OrderSide.BUY:
            cost = execution_price * quantity + commission_amount
            if cost > self.capital:
                self.logger.logger.warning(f"Insufficient capital for buy order")
                return

            # Record position
            self.positions[symbol] = {
                "side": side,
                "quantity": quantity,
                "entry_price": execution_price,
                "entry_time": signal.timestamp,
                "current_price": execution_price,
                "unrealized_pnl": Decimal("0"),
            }

            self.capital -= cost

        else:  # SELL
            # Check if we have position to sell
            if symbol not in self.positions:
                # Short selling not supported in this simple backtest
                self.logger.logger.warning(f"No position to sell for {symbol}")
                return

            position = self.positions[symbol]
            sell_quantity = min(quantity, position["quantity"])

            # Calculate P&L
            if position["side"] == OrderSide.BUY:
                pnl = (
                    execution_price - position["entry_price"]
                ) * sell_quantity - commission_amount
            else:
                pnl = (
                    position["entry_price"] - execution_price
                ) * sell_quantity - commission_amount

            return_pct = (
                float(pnl / (position["entry_price"] * sell_quantity) * 100)
                if position["entry_price"] > 0
                else 0
            )

            # Record trade
            trade = Trade(
                entry_time=position["entry_time"],
                exit_time=signal.timestamp,
                symbol=symbol,
                side=position["side"],
                entry_price=position["entry_price"],
                exit_price=execution_price,
                quantity=sell_quantity,
                pnl=pnl,
                return_pct=return_pct,
            )
            self.trades.append(trade)

            # Update capital
            proceeds = execution_price * sell_quantity - commission_amount
            self.capital += proceeds + pnl

            # Update or remove position
            position["quantity"] -= sell_quantity
            if position["quantity"] <= 0:
                del self.positions[symbol]

    def _calculate_equity(self, data: Dict[str, pd.DataFrame]) -> Decimal:
        """Calculate total equity (cash + positions)."""
        equity = self.capital

        for symbol, position in self.positions.items():
            if symbol in data and not data[symbol].empty:
                current_price = Decimal(str(data[symbol]["close"].iloc[-1]))
                equity += position["quantity"] * current_price

        return equity

    def _close_all_positions(self, final_time: datetime, data: Dict[str, pd.DataFrame]) -> None:
        """Close all open positions at the end of backtest."""
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]

            if symbol in data and not data[symbol].empty:
                final_price = Decimal(str(data[symbol]["close"].iloc[-1]))

                # Apply slippage and commission
                execution_price = final_price * (1 - Decimal(str(self.slippage)))
                commission_amount = (
                    execution_price * position["quantity"] * Decimal(str(self.commission))
                )

                # Calculate P&L
                if position["side"] == OrderSide.BUY:
                    pnl = (execution_price - position["entry_price"]) * position[
                        "quantity"
                    ] - commission_amount
                else:
                    pnl = (position["entry_price"] - execution_price) * position[
                        "quantity"
                    ] - commission_amount

                return_pct = float(pnl / (position["entry_price"] * position["quantity"]) * 100)

                # Record trade
                trade = Trade(
                    entry_time=position["entry_time"],
                    exit_time=final_time,
                    symbol=symbol,
                    side=position["side"],
                    entry_price=position["entry_price"],
                    exit_price=execution_price,
                    quantity=position["quantity"],
                    pnl=pnl,
                    return_pct=return_pct,
                )
                self.trades.append(trade)

                # Update capital
                proceeds = execution_price * position["quantity"]
                self.capital += proceeds - commission_amount

        self.positions.clear()
