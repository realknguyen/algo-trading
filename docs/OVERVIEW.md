# Repository Overview

## What This Repo Is

This is a trading workspace with:

1. A lightweight strategy/backtest path for fast local iteration
2. A top-level async trading stack for exchange adapters, order management, risk controls, and database-backed operations

The CLI now exposes both in a controlled way.

## What Is Verified

- `python main.py list-strategies`
- `python main.py backtest --help`
- `python main.py init-db --help`
- `python main.py paper --help`
- `python main.py live --help`
- `pytest -q`

## Operational Reality

- `paper` and `live` are now real CLI commands, but they are safe-by-default signal runners unless explicit execution flags are supplied.
- Live order placement is guarded, not implicit.
- Exchange adapters still need venue-specific sandbox validation before operational trust.

## Practical Guidance

- Use `backtest` for fast local strategy iteration.
- Use `paper` to exercise the top-level async runtime against sandbox/test environments.
- Use `live` for guarded live-endpoint polling, and only enable order execution intentionally.
- Keep new behavior aligned with the shipped CLI instead of creating a third execution surface.
