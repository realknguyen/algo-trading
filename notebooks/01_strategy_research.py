"""Example notebook for strategy research."""

# %% [markdown]
# # Algorithmic Trading Research
# 
# This notebook demonstrates how to use the algo-trading framework for research.

# %%
import sys
sys.path.insert(0, '..')

import pandas as pd
import matplotlib.pyplot as plt
from src.data.fetcher import DataFetcher
from src.strategy.sma_crossover import SmaCrossoverStrategy
from src.backtest.runner import BacktestRunner

# %% [markdown]
# ## 1. Fetch Historical Data

# %%
fetcher = DataFetcher(cache_dir='../data/cache')
df = fetcher.fetch('AAPL', period='2y', interval='1d')
print(f"Fetched {len(df)} rows of data")
df.head()

# %% [markdown]
# ## 2. Visualize Price Data

# %%
plt.figure(figsize=(12, 6))
plt.plot(df.index, df['close'], label='Close Price')
plt.title('AAPL Stock Price')
plt.xlabel('Date')
plt.ylabel('Price ($)')
plt.legend()
plt.show()

# %% [markdown]
# ## 3. Run SMA Crossover Strategy

# %%
strategy = SmaCrossoverStrategy({'fast_period': 20, 'slow_period': 50})
runner = BacktestRunner(initial_capital=100000)
result = runner.run(strategy, df, symbol='AAPL')

# %%
print(result.summary())

# %% [markdown]
# ## 4. Plot Equity Curve

# %%
plt.figure(figsize=(12, 6))
plt.plot(result.equity_curve.index, result.equity_curve.values)
plt.title('Strategy Equity Curve')
plt.xlabel('Date')
plt.ylabel('Portfolio Value ($)')
plt.show()

# %% [markdown]
# ## 5. Analyze Trades

# %%
if result.trades:
    trades_df = pd.DataFrame([
        {
            'Entry': t.entry_date,
            'Exit': t.exit_date,
            'Side': t.side,
            'Entry Price': t.entry_price,
            'Exit Price': t.exit_price,
            'P&L': t.pnl,
            'Return %': t.return_pct
        }
        for t in result.trades
    ])
    print(trades_df)
