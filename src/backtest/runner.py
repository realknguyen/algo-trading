"""Backtesting engine for strategy evaluation."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
import pandas as pd
import numpy as np


@dataclass
class Trade:
    """Represents a completed trade."""
    entry_date: datetime
    exit_date: datetime
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    
    # Performance metrics
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    
    def calculate_metrics(self) -> Dict[str, float]:
        """Calculate performance metrics."""
        if not self.trades:
            return {}
        
        returns = [t.return_pct for t in self.trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        
        self.win_rate = len(wins) / len(returns) if returns else 0
        
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        self.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # Calculate max drawdown from equity curve
        if not self.equity_curve.empty:
            rolling_max = self.equity_curve.cummax()
            drawdown = (self.equity_curve - rolling_max) / rolling_max
            self.max_drawdown_pct = drawdown.min() * 100
            self.max_drawdown = drawdown.min() * self.initial_capital
            
            # Sharpe ratio (assuming risk-free rate of 0 for simplicity)
            daily_returns = self.equity_curve.pct_change().dropna()
            if len(daily_returns) > 1 and daily_returns.std() > 0:
                self.sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
        
        return {
            'total_return_pct': self.total_return_pct,
            'win_rate': self.win_rate * 100,
            'profit_factor': self.profit_factor,
            'max_drawdown_pct': self.max_drawdown_pct,
            'sharpe_ratio': self.sharpe_ratio,
            'num_trades': len(self.trades)
        }
    
    def summary(self) -> str:
        """Generate a summary report."""
        metrics = self.calculate_metrics()
        
        report = f"""
{'='*50}
Backtest Results: {self.strategy_name}
{'='*50}
Period: {self.start_date.date()} to {self.end_date.date()}
Initial Capital: ${self.initial_capital:,.2f}
Final Capital: ${self.final_capital:,.2f}
Total Return: {self.total_return_pct:.2f}%
{'-'*50}
Performance Metrics:
  Win Rate: {metrics.get('win_rate', 0):.1f}%
  Profit Factor: {metrics.get('profit_factor', 0):.2f}
  Max Drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%
  Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}
  Number of Trades: {metrics.get('num_trades', 0)}
{'='*50}
"""
        return report


class BacktestRunner:
    """Run backtests for trading strategies."""
    
    def __init__(self, initial_capital: float = 100000.0, commission: float = 0.001):
        self.initial_capital = initial_capital
        self.commission = commission
    
    def run(
        self,
        strategy,
        data: pd.DataFrame,
        symbol: str = "UNKNOWN"
    ) -> BacktestResult:
        """
        Run a backtest for a given strategy and data.
        
        Args:
            strategy: Strategy instance with on_data method
            data: OHLCV DataFrame
            symbol: Symbol being traded
        
        Returns:
            BacktestResult with performance metrics
        """
        capital = self.initial_capital
        position = 0  # 0 = flat, 1 = long
        entry_price = 0.0
        entry_date = None
        trades = []
        equity = [capital]
        
        # Initialize strategy
        strategy.initialize(data.iloc[:50] if len(data) > 50 else data)
        
        for i in range(50, len(data)):
            window = data.iloc[:i+1]
            current = data.iloc[i]
            
            signal = strategy.on_data(window)
            
            # Process signal
            if signal:
                if signal.action == 'buy' and position == 0:
                    # Enter long position
                    entry_price = current['close'] * (1 + self.commission)
                    entry_date = current.name if isinstance(current.name, datetime) else data.index[i]
                    position = 1
                    
                elif signal.action == 'sell' and position == 1:
                    # Exit long position
                    exit_price = current['close'] * (1 - self.commission)
                    exit_date = current.name if isinstance(current.name, datetime) else data.index[i]
                    
                    # Calculate P&L
                    pnl = exit_price - entry_price
                    return_pct = (exit_price - entry_price) / entry_price * 100
                    
                    trade = Trade(
                        entry_date=entry_date,
                        exit_date=exit_date,
                        symbol=symbol,
                        side='long',
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=1.0,
                        pnl=pnl,
                        return_pct=return_pct
                    )
                    trades.append(trade)
                    
                    capital *= (1 + return_pct / 100)
                    position = 0
            
            # Update equity curve
            if position == 1:
                unrealized = (current['close'] - entry_price) / entry_price
                equity.append(capital * (1 + unrealized))
            else:
                equity.append(capital)
        
        # Close any open position at the end
        if position == 1:
            final_price = data.iloc[-1]['close'] * (1 - self.commission)
            final_date = data.index[-1]
            pnl = final_price - entry_price
            return_pct = (final_price - entry_price) / entry_price * 100
            
            trade = Trade(
                entry_date=entry_date,
                exit_date=final_date,
                symbol=symbol,
                side='long',
                entry_price=entry_price,
                exit_price=final_price,
                quantity=1.0,
                pnl=pnl,
                return_pct=return_pct
            )
            trades.append(trade)
            capital *= (1 + return_pct / 100)
        
        result = BacktestResult(
            strategy_name=strategy.name,
            start_date=data.index[0] if isinstance(data.index[0], datetime) else datetime.now(),
            end_date=data.index[-1] if isinstance(data.index[-1], datetime) else datetime.now(),
            initial_capital=self.initial_capital,
            final_capital=capital,
            total_return=capital - self.initial_capital,
            total_return_pct=(capital - self.initial_capital) / self.initial_capital * 100,
            trades=trades,
            equity_curve=pd.Series(equity, index=data.index[:len(equity)])
        )
        
        return result


if __name__ == "__main__":
    print("Backtest module loaded. Use BacktestRunner to run backtests.")
