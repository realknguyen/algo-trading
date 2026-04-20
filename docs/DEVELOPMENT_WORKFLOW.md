# Development Workflow

This document explains how to work in the repository from day one, including how to add strategies, test them, and move toward the guarded runtime flows.

## 1. Start With The Supported Path

The first thing to remember is that the repo already has a shipped contract:

```text
main.py -> src/cli.py
```

If you stay aligned with that path, your work will be easier to test, easier to explain, and less likely to drift from what the repo actually supports.

## 2. Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## 3. Useful Daily Commands

```bash
make compile
make smoke
make test
make verify
```

Use these narrower commands when you want quicker feedback:

```bash
pytest -q tests/unit
pytest -q tests/integration
pytest -q tests/unit/test_cli.py
pytest -q tests/unit/test_top_level_runtime.py
python main.py list-strategies
python main.py backtest --help
```

## 4. Choosing The Right Surface Before Coding

Ask this first:

### Are You Building A Lightweight Strategy?

If you want:

- fast local iteration
- local backtesting
- simpler DataFrame-oriented logic

then work in:

- `src/strategy/`
- `src/backtest/`
- `src/data/`

### Are You Building A Runtime Strategy For `paper/live`?

If you want:

- strategy execution through the shipped runtime
- OMS integration
- top-level risk integration

then you need:

- an implementation in `algorithms/`
- runtime wiring in `src/runtime/top_level.py`

### Are You Working On Adapters?

Read [ADAPTER_SURFACES.md](ADAPTER_SURFACES.md) first. Do not assume `adapters/` and `src/adapters/` are interchangeable.

## 5. Standard Flow For A New Lightweight Strategy

### Step 1: Create The Strategy

Add a new strategy module under `src/strategy/`.

It should implement the `BaseStrategy` interface:

- `initialize(data)`
- `on_data(data)`
- `get_parameters()`

### Step 2: Register The Strategy

Update the registry in `src/strategy/__init__.py`.

If you do not register it, the CLI will not see it.

### Step 3: Add Focused Tests

Add unit tests that check:

- default parameters
- custom parameter overrides
- signal generation conditions
- edge cases such as insufficient data
- any risk metadata the strategy emits

Good reference:

- `tests/unit/test_strategy_sma_crossover_risk.py`

### Step 4: Add Or Update Integration Coverage

If the strategy changes how backtesting behaves, add or update an integration-style backtest test.

Good reference:

- `tests/integration/test_backtest.py`

### Step 5: Verify It End To End

Run the smallest useful checks:

```bash
pytest -q tests/unit/test_your_strategy.py
python main.py list-strategies
python main.py backtest --strategy your_strategy --symbol BTCUSDT --period 3mo
```

If the strategy depends on exact date ranges or cache behavior, test that too.

## 6. Standard Flow For A Runtime Strategy

Runtime strategy work has one extra step that often surprises contributors.

### Step 1: Implement The Runtime Strategy

Add the strategy under `algorithms/` using `BaseAlgorithm`.

That means handling:

- initialization over symbol-to-DataFrame inputs
- signal generation
- runtime execution hooks
- order fill handling

### Step 2: Wire It Into The Runtime Bridge

Update `src/runtime/top_level.py` so the bridge knows how to build the strategy from CLI/runtime params.

If you skip this, `paper/live` will not know your strategy exists.

### Step 3: Add Runtime Tests

At minimum, add or update tests around:

- strategy parameter coercion
- dry-run behavior
- execution-enabled behavior
- runtime summary or signal behavior if relevant

### Step 4: Validate In Stages

Recommended progression:

```bash
python main.py paper --strategy your_strategy --exchange binance --symbols BTCUSDT --iterations 1
python main.py paper --strategy your_strategy --exchange binance --symbols BTCUSDT --execute-orders --iterations 1
python main.py live --strategy your_strategy --exchange binance --symbols BTCUSDT --iterations 1
```

Only then consider:

```bash
python main.py live --strategy your_strategy --exchange binance --symbols BTCUSDT --execute-orders --confirm-live --iterations 1
```

## 7. Backtest Workflow

The lightweight backtest flow is:

1. fetch OHLCV market data with `src/data/fetcher.py`
2. load or write local cache files in `data/cache`
3. create a lightweight strategy from the registry
4. run the backtest in `src/backtest/runner.py`
5. optionally write the result payload to JSON

Examples:

```bash
python main.py backtest --strategy sma_crossover --symbol BTCUSDT --period 1y
python main.py backtest --strategy sma_crossover_risk --symbol BTCUSDT --start 2024-01-01 --end 2024-03-01 --output out/result.json
```

Useful flags:

- `--fast-period`
- `--slow-period`
- `--param key=value`
- `--cache-dir`
- `--no-cache`
- `--output`

## 8. Runtime Workflow

The top-level runtime flow is:

1. validate CLI args
2. resolve config from environment
3. build the top-level adapter, risk manager, order manager, and algorithm
4. fetch candles for the requested symbols
5. initialize the algorithm
6. run polling iterations
7. emit signals
8. optionally execute orders when allowed and explicitly enabled

Important:

- `paper` and `live` are dry-run by default
- live order execution requires `--confirm-live`
- credentials are required if order execution is enabled

## 9. Database Workflow

The database schema is initialized through the CLI:

```bash
python main.py init-db
```

For local testing without a running Postgres instance:

```bash
python main.py init-db --database-url sqlite:///./local.db
```

Use this when:

- checking schema creation behavior
- validating a change to database bootstrap code
- running smoke tests

## 10. Testing Strategy

### Smallest Relevant Verification First

Prefer:

- one targeted unit test file
- one focused integration file
- one CLI smoke command

before:

- full `make verify`
- broad multi-minute verification runs

### Typical Verification Patterns

For a strategy change:

```bash
pytest -q tests/unit/test_strategy_*.py
pytest -q tests/integration/test_backtest.py
```

For a CLI/runtime change:

```bash
pytest -q tests/unit/test_cli.py tests/unit/test_top_level_runtime.py
make smoke
```

For a database bootstrap change:

```bash
pytest -q tests/unit/test_runtime_database.py
python main.py init-db --database-url sqlite:///./tmp.db
```

## 11. Common Mistakes To Avoid

- adding a lightweight strategy and assuming `paper/live` will support it automatically
- editing the wrong adapter surface
- changing `risk_management/` casually
- documenting features that are not reachable from the shipped CLI
- creating a new abstraction layer instead of using an existing one

## 12. Recommended Contributor Mindset

- stay close to the supported runtime surface
- keep code simple
- keep docs current
- prefer verification you can explain clearly
- avoid overclaiming production readiness
