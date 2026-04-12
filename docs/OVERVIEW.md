# Repository Overview

## What This Repo Contains

This repository currently has two overlapping implementation surfaces:

1. `adapters/`, `algorithms/`, `order_management/`, `risk_management/`, `backtesting/`, `database/`, and `main.py`
   These form the top-level async trading runtime.
2. `src/`
   This is a lightweight compatibility layer used by the current unit and integration tests for strategy logic, backtesting, risk checks, and broker shims.

That split is the main architectural wrinkle in the repo today. Both surfaces now work, but they are not yet fully unified.

## Verified Today

- The default test suite under `tests/` passes with `pytest -q`.
- The installed CLI entry point points to `main:main`.
- Top-level runtime imports succeed.
- The lightweight `src/` brokers, strategy, risk manager, execution engine, and backtest runner all have test coverage.

## Important Reality Checks

- `paper` mode is scaffolded, not a fully automated production paper-trading loop.
- `live` mode is not implemented beyond a confirmation prompt and a guardrail log message.
- Exchange adapters exist, but that should not be read as proof that every venue has been recently validated end to end.

## Practical Layout

```text
.
├── adapters/               # Async exchange adapters for the top-level runtime
├── algorithms/             # BaseAlgorithm, QC adapter, and top-level strategies
├── backtesting/            # Async backtesting engine used by the top-level runtime
├── config/                 # Pydantic settings and example config
├── database/               # SQLAlchemy models and Alembic setup
├── order_management/       # Top-level order manager
├── risk_management/        # Top-level risk engine
├── src/
│   ├── backtest/           # Lightweight backtest runner
│   ├── broker/             # Lightweight test broker shims
│   ├── execution/          # Lightweight execution engine
│   ├── risk/               # Lightweight risk manager
│   └── strategy/           # Lightweight strategies used by tests
├── tests/                  # Unit and integration tests
└── trading_logging/        # Shared logging helpers
```

## Where To Add New Work

- Add new production-facing exchange/runtime work to the top-level async stack.
- Add compatibility fixes for the existing test harness under `src/` only when needed to keep tests and legacy examples working.
- If you are starting a new feature, prefer not to create a third path.

## Recommended Near-Term Priorities

1. Consolidate the duplicated top-level and `src/` trading surfaces.
2. Add CI so `pytest -q` runs automatically on every change.
3. Add sandbox-backed exchange verification for the adapters that are intended to be used operationally.
4. Either finish `paper` and `live` flows or document them as intentionally partial.
