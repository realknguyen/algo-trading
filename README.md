# Algorithmic Trading Platform

A Python-based algorithmic trading framework for strategy development, backtesting, and live trading.

## Features

- **Strategy Engine**: Modular strategy implementation with SMA crossover sample
- **Data Ingestion**: Market data fetching via yfinance with local caching
- **Backtesting**: Historical performance testing with metrics
- **Risk Management**: Position sizing and exposure controls
- **Broker Integration**: Extensible broker API wrappers (Alpaca, Interactive Brokers)
- **Execution Engine**: Order management and routing

## Quick Start

### 1. Setup Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Keys

Copy the config template and add your API credentials:

```bash
cp config/config.yaml.example config/config.yaml
# Edit config.yaml with your API keys
```

### 3. Run a Sample Backtest

```bash
python -m src.backtest.runner --strategy sma_crossover --symbol AAPL --start 2023-01-01 --end 2024-01-01
```

## Project Structure

```
algo-trading/
├── src/
│   ├── broker/          # Broker API wrappers
│   ├── strategy/        # Trading strategies
│   ├── data/            # Data ingestion & caching
│   ├── backtest/        # Backtesting engine
│   ├── risk/            # Risk management
│   └── execution/       # Order management
├── config/              # Configuration files (gitignored)
├── data/cache/          # Market data cache
├── notebooks/           # Research notebooks
└── tests/               # Unit tests
```

## Configuration

Edit `config/config.yaml` to set:
- Broker API credentials
- Risk limits (max position size, max drawdown)
- Data provider settings
- Strategy parameters

## Strategies

### SMA Crossover (Sample)

A simple moving average crossover strategy included as an example.

- **Fast SMA**: 20 periods
- **Slow SMA**: 50 periods
- **Entry**: Fast crosses above Slow
- **Exit**: Fast crosses below Slow

## Development

```bash
# Run tests
pytest tests/

# Format code
black src/ tests/

# Type checking
mypy src/
```

## Disclaimer

This software is for educational purposes only. Use at your own risk. Always test strategies thoroughly with backtesting before deploying live capital.

## License

MIT License - See LICENSE file for details.
