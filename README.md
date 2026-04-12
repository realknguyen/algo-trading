# Algo Trading

Python trading workspace with two actively used codepaths:

- A top-level async runtime in `adapters/`, `algorithms/`, `order_management/`, `risk_management/`, `backtesting/`, and `database/`.
- A lightweight compatibility/test harness in `src/` used by the current unit and integration suite.

The repository is now wired so both surfaces import cleanly, the console entry point is valid, and the default test suite passes.

## Current Status

- `pytest -q` passes locally in this repository.
- `algo-trade` now resolves to `main:main`.
- Structured logging lives in `trading_logging/`.
- `paper` mode is scaffolded but still simplified.
- `live` mode is intentionally not implemented beyond a confirmation guard.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[dev]"
```

If you prefer a flat requirements install:

```bash
pip install -r requirements.txt
```

## Verification

```bash
pytest -q
```

## CLI

The CLI is available through either `python main.py` or the installed `algo-trade` entry point.

### Backtest

```bash
algo-trade backtest \
  --strategy sma_crossover \
  --symbols AAPL MSFT \
  --start 2023-01-01 \
  --end 2024-01-01
```

### Paper Trading

```bash
algo-trade paper \
  --strategy sma_crossover \
  --symbols BTCUSDT \
  --interval 1h
```

Paper mode currently connects, builds the runtime objects, and enters a placeholder loop. It is useful for integration work, but it is not yet a complete unattended paper-trading engine.

### Live Trading

```bash
algo-trade live --strategy sma_crossover --symbols BTCUSDT
```

Live mode stops after an explicit confirmation prompt and currently logs that the implementation is not finished.

### Database Initialization

```bash
algo-trade init-db
```

## Repository Map

```text
.
├── adapters/           # Async exchange adapters used by the top-level runtime
├── algorithms/         # Top-level algorithm framework and QC adapter support
├── backtesting/        # Top-level async backtesting engine
├── config/             # Pydantic settings and example config
├── database/           # SQLAlchemy models and migrations
├── docs/               # Project documentation
├── order_management/   # Top-level OMS
├── risk_management/    # Top-level risk engine
├── src/                # Lightweight compatibility/test harness
├── tests/              # Unit and integration suite
└── trading_logging/    # Shared logging helpers
```

## Development Guidance

- Prefer the top-level async runtime for new production-facing work.
- Keep the `src/` compatibility layer green until the repo is consolidated onto one public runtime surface.
- Do not modify `risk_management/` rules casually; this repo treats risk logic as protected behavior.

## Safety Notes

- Rate limits and circuit-breaker concepts exist in the codebase, but you should still treat all exchange integrations as requiring sandbox verification before real capital is involved.
- The repo contains exchange adapters and execution code. That does not mean every path has been validated end to end against a live venue.
- Trading software can lose money quickly. Verify environment config, adapter behavior, and order routing in sandbox/testnet before using any real credentials.
