# Algo Trading

Python trading workspace with two connected layers:

- A lightweight CLI/backtest surface under `src/`
- A broader async trading stack under `adapters/`, `algorithms/`, `order_management/`, `risk_management/`, `backtesting/`, and `database/`

The shipped CLI now bridges both surfaces instead of pretending they are unrelated.

## Current CLI Surface

The installed entry point is `algo-trade = "main:main"`, which supports:

- `list-strategies`
- `backtest`
- `init-db`
- `paper`
- `live`

## Status

- `pytest -q` passes locally.
- Database initialization is executable from the CLI.
- `paper` and `live` are safe-by-default polling runners that bridge into the top-level async stack.
- `paper` and `live` run in signal-only dry-run mode unless `--execute-orders` is supplied.
- Live order execution requires explicit `--confirm-live` and valid exchange credentials.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[dev]"
```

## Quickstart

List strategies:

```bash
python main.py list-strategies
```

Run a lightweight backtest:

```bash
python main.py backtest \
  --strategy sma_crossover_risk \
  --symbol BTCUSDT \
  --start 2024-01-01 \
  --end 2024-03-01
```

Initialize the database schema:

```bash
python main.py init-db
```

Run the top-level async runtime in paper-mode dry-run:

```bash
python main.py paper \
  --exchange binance \
  --symbols BTCUSDT ETHUSDT \
  --interval 1h \
  --iterations 3 \
  --poll-seconds 30
```

Run the same runtime against live market endpoints without placing orders:

```bash
python main.py live \
  --exchange binance \
  --symbols BTCUSDT \
  --interval 1h \
  --iterations 1
```

Submit live orders only when you explicitly intend to:

```bash
python main.py live \
  --exchange binance \
  --symbols BTCUSDT \
  --execute-orders \
  --confirm-live
```

## Developer Workflow

Common local commands:

```bash
make compile
make smoke
make test
make verify
```

`make smoke` checks the shipped CLI contract, including database init and runtime help surfaces.

## Repository Map

```text
.
├── adapters/           # Top-level async exchange adapters
├── algorithms/         # Top-level async strategy framework
├── backtesting/        # Top-level async backtesting engine
├── config/             # Pydantic settings and example config
├── database/           # SQLAlchemy models and Alembic wiring
├── docs/               # Architecture and production-readiness docs
├── order_management/   # Top-level OMS
├── risk_management/    # Top-level risk engine
├── src/                # Lightweight CLI/backtest surface plus runtime bridges
├── tests/              # Unit and integration tests
└── trading_logging/    # Shared logging bootstrap and redaction helpers
```

## Safety Notes

- Do not treat this repository as blanket approval for unattended live trading.
- `live` is now executable, but real order placement remains explicitly guarded.
- Validate credentials, adapter behavior, and venue-specific semantics in sandbox/testnet before real capital use.
- `risk_management/` is protected behavior and should only be changed deliberately.

## Further Reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/OVERVIEW.md](docs/OVERVIEW.md)
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
