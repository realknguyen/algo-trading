# Algo Trading

Python trading workspace with two connected implementation layers:

- A lightweight `src/` surface for fast local strategy iteration, backtesting, and simple test harnesses
- A broader top-level async trading surface for exchange adapters, order management, risk controls, and database-backed runtime flows

The shipped CLI bridges both layers through one entrypoint:

```text
main.py -> src/cli.py
```

This repository is useful today for:

- developing and iterating on strategies locally
- backtesting against cached Yahoo Finance market data
- initializing the database schema used by the broader runtime
- exercising guarded paper/live polling flows against the supported top-level exchange adapters

It should not be treated as blanket approval for unattended live-capital trading.

## What The Repo Can Do Today

### Supported CLI Commands

The installed script entrypoint is `algo-trade = "main:main"`. The same surface is available with `python main.py ...`.

Currently supported commands:

- `list-strategies`
- `backtest`
- `init-db`
- `paper`
- `live`

### What Each Command Actually Uses

| Command | Primary Code Path | Purpose |
|---|---|---|
| `list-strategies` | `src/strategy` | List lightweight strategy registry entries |
| `backtest` | `src/data` + `src/strategy` + `src/backtest` | Fast local strategy iteration |
| `init-db` | `config` + `database` via `src/runtime/database.py` | Create the configured schema |
| `paper` | `src/runtime/top_level.py` + top-level `adapters/`, `algorithms/`, `order_management/`, `risk_management/` | Safe-by-default polling runtime against sandbox/test environments |
| `live` | Same runtime bridge as `paper`, but using live endpoints | Guarded live polling; order submission requires explicit opt-in |

### Current Operational Reality

- `paper` and `live` are real commands, not placeholders.
- Both commands default to signal-only dry-run mode.
- Real order submission only happens when `--execute-orders` is enabled.
- `live` order submission requires `--execute-orders --confirm-live`.
- The top-level runtime currently supports `sma_crossover` as its shipped runtime strategy.
- The lightweight backtest surface currently exposes `sma_crossover` and `sma_crossover_risk`.

## Quick Start

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[dev]"
```

### 2. Explore The CLI

```bash
python main.py list-strategies
python main.py backtest --help
python main.py paper --help
python main.py live --help
```

### 3. Run A Lightweight Backtest

```bash
python main.py backtest \
  --strategy sma_crossover_risk \
  --symbol BTCUSDT \
  --start 2024-01-01 \
  --end 2024-03-01
```

Notes:

- Historical data is fetched through `yfinance`.
- Downloaded data is cached locally under `data/cache` unless `--no-cache` is supplied.
- You can use `--period` instead of `--start/--end`.
- You can pass extra strategy params with repeatable `--param key=value`.

Example:

```bash
python main.py backtest \
  --strategy sma_crossover_risk \
  --symbol BTCUSDT \
  --period 6mo \
  --interval 1d \
  --param fast_period=12 \
  --param slow_period=40 \
  --param volatility_filter=4.0 \
  --output out/backtests/btc_sma_risk.json
```

### 4. Initialize The Database

```bash
python main.py init-db
```

For a local smoke test against SQLite:

```bash
python main.py init-db --database-url sqlite:///./local-trading.db
```

### 5. Exercise The Top-Level Runtime In Dry Run

Paper-mode dry run:

```bash
python main.py paper \
  --exchange binance \
  --symbols BTCUSDT ETHUSDT \
  --interval 1h \
  --iterations 2 \
  --poll-seconds 30
```

Live-endpoint dry run:

```bash
python main.py live \
  --exchange binance \
  --symbols BTCUSDT \
  --interval 1h \
  --iterations 1
```

### 6. Enable Order Submission Only Intentionally

Paper/runtime execution:

```bash
python main.py paper \
  --exchange binance \
  --symbols BTCUSDT \
  --execute-orders \
  --iterations 1
```

Live/runtime execution:

```bash
python main.py live \
  --exchange binance \
  --symbols BTCUSDT \
  --execute-orders \
  --confirm-live \
  --iterations 1
```

The live path requires valid exchange credentials in the environment and should be validated in sandbox/testnet conditions first.

## How To Think About The Repo

### There Are Two Main Surfaces

#### 1. Lightweight `src/` Surface

Use this when you want:

- fast strategy iteration
- local backtesting
- simpler unit and integration tests
- easy-to-understand examples of signal generation, risk evaluation, and execution

Key packages:

- `src/strategy/`
- `src/backtest/`
- `src/data/`
- `src/risk/`
- `src/execution/`
- `src/broker/`

#### 2. Top-Level Async Surface

Use this when you want:

- richer exchange adapters
- the shipped `paper` and `live` runtime flows
- order management with the top-level OMS
- broader risk semantics
- database-backed runtime services

Key packages:

- `adapters/`
- `algorithms/`
- `order_management/`
- `risk_management/`
- `database/`
- `trading_logging/`
- `config/`

### Why This Matters

If you add a strategy only to `src/strategy`, it can be listed and backtested, but it is not automatically available to `paper` and `live`.

If you want a strategy to run in the top-level runtime, you also need:

- an implementation under `algorithms/`
- wiring in `src/runtime/top_level.py`

## The Difference Between `adapters/` And `src/adapters/`

Short version:

- `adapters/` is the adapter surface used by the current shipped `paper/live` runtime.
- `src/adapters/` is a broader, newer, more toolkit-oriented adapter surface that is not the main CLI runtime dependency today.

In practice:

- The main CLI runtime imports from top-level `adapters/`.
- The top-level OMS and algorithms also import from top-level `adapters/`.
- `src/adapters/` contains enhanced or extended adapter infrastructure, extra venue work, auth/signing helpers, normalizers, and targeted adapter utilities.
- `src/adapters/` is important code, but it is not the default runtime surface a new contributor should reach for first unless they are working specifically in that subsystem.

If you are unsure which adapter surface to touch:

- for `paper/live` support, start with `adapters/`
- for the shipped runtime bridge, stay aligned with `adapters/`
- for targeted extended-adapter work such as Hyperliquid, Bybit, auth/signing utilities, or normalizers, work in `src/adapters/`

There is a dedicated explanation in [docs/ADAPTER_SURFACES.md](docs/ADAPTER_SURFACES.md).

## Repository Map

```text
.
├── adapters/           # Top-level async exchange adapters used by the shipped runtime
├── algorithms/         # Top-level async strategy framework used by paper/live
├── backtesting/        # Broader top-level backtesting/engine code
├── config/             # Pydantic settings and example config
├── database/           # SQLAlchemy models and migration wiring
├── docs/               # User-facing architecture, workflow, and readiness docs
├── order_management/   # Top-level OMS
├── risk_management/    # Top-level risk engine used by shipped runtime execution
├── src/                # Lightweight local stack plus runtime bridges
│   ├── adapters/       # Extended adapter toolkit and auxiliary adapter surface
│   ├── backtest/       # Lightweight backtest engine
│   ├── broker/         # Lightweight broker abstractions for tests and local flows
│   ├── data/           # Market data fetching and cache helpers
│   ├── execution/      # Lightweight execution boundary
│   ├── risk/           # Lightweight risk manager used by local flows
│   ├── runtime/        # Bridges from CLI into top-level runtime/database surfaces
│   └── strategy/       # Lightweight strategy registry and implementations
├── tests/              # Unit and integration tests
└── trading_logging/    # Shared logging bootstrap and redaction helpers
```

## Recommended Development Flow

### If You Are Implementing A New Strategy

For a lightweight/backtest-only strategy:

1. Add the strategy in `src/strategy/`.
2. Register it in `src/strategy/__init__.py`.
3. Add unit tests in `tests/unit/`.
4. Add or extend a backtest/integration test.
5. Verify with `python main.py list-strategies` and `python main.py backtest ...`.

For a strategy that should also work in `paper/live`:

1. Implement the lightweight strategy if you want fast local backtesting.
2. Implement the runtime strategy under `algorithms/`.
3. Wire the runtime strategy into `src/runtime/top_level.py`.
4. Add runtime-focused tests.
5. Validate in `paper` dry-run mode before touching order execution.

### If You Are Working On Exchange Runtime Behavior

1. Check whether the shipped runtime uses the top-level `adapters/` path for that change.
2. Avoid introducing a third adapter abstraction or runtime surface.
3. Validate with the smallest relevant runtime or adapter tests.
4. Prefer sandbox/testnet validation before claiming live readiness.

### If You Are Working On Risk Or Execution

- Treat `risk_management/` as protected behavior.
- Keep risk semantics consistent across lightweight and top-level surfaces.
- Fail closed when in doubt.
- Add explicit tests around trading-disabled and oversize-order cases.

## Common Commands

```bash
make compile
make smoke
make test
make verify
```

What they do:

- `make compile`: import and bytecode health across the main packages
- `make smoke`: checks the shipped CLI contract, including `init-db`, `paper --help`, and `live --help`
- `make test`: runs `pytest -q`
- `make verify`: runs compile checks, smoke checks, and the default test suite

More detailed contributor guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md).

## Configuration

Runtime configuration is read through `config/settings.py`, primarily from environment variables.

Important config groups:

- `DB_*` for database settings
- `BINANCE_*` for Binance credentials and connection settings
- `KRAKEN_*` for Kraken credentials
- `COINBASE_*` for Coinbase credentials
- `ALPACA_*` for Alpaca credentials
- `RISK_*` for risk settings
- `LOG_*` for logging settings

Examples:

- `DATABASE_URL` can override the derived database DSN
- `BINANCE_API_KEY` and `BINANCE_API_SECRET` are needed when executing Binance orders
- `RISK_MAX_POSITION_SIZE`, `RISK_DAILY_LOSS_LIMIT`, and similar settings control top-level runtime risk behavior

See:

- [config/settings.py](config/settings.py)
- [config/config.yaml.example](config/config.yaml.example)

## Testing And Verification

### Default Test Paths

- `tests/unit/` contains most direct local coverage
- `tests/integration/` contains broader flow tests
- `pytest -q` is the normal default local suite

### What Is Well Covered Today

- CLI argument handling
- lightweight strategies and backtesting
- runtime bridge dry-run and execution-enabled behavior
- settings hardening
- lightweight risk and execution primitives

### What Still Needs Care

- venue-specific operational certification
- all extended adapter surfaces
- end-to-end live deployment readiness

## Safety Notes

- Do not assume `live` means “production-certified.”
- Validate exchange semantics in sandbox/testnet before real capital use.
- Do not change `risk_management/` casually.
- Circuit breakers and risk rejections should be treated as real blockers, not nuisances.
- Prefer dry-run and paper validation before enabling execution.

## Documentation Index

- [docs/OVERVIEW.md](docs/OVERVIEW.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md)
- [docs/ADAPTER_SURFACES.md](docs/ADAPTER_SURFACES.md)
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)
- [docs/TASKS.md](docs/TASKS.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
