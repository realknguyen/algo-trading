# Repository Overview

## Purpose

This repository is a trading workspace that combines:

1. a lightweight local strategy/backtest surface for fast iteration
2. a broader async runtime surface for exchange adapters, order management, risk controls, logging, and database-backed operations

The current CLI exposes both surfaces through one entrypoint rather than pretending they are unrelated.

## Who This Repo Is For

This repo is most useful for:

- contributors building or testing trading strategies locally
- engineers integrating exchange adapters into the shipped runtime
- maintainers hardening execution, risk, configuration, or database behavior
- developers who want a gradual path from local backtests to guarded paper/live runtime flows

## What Is Executable Today

The supported command surface is:

- `python main.py list-strategies`
- `python main.py backtest ...`
- `python main.py init-db`
- `python main.py paper ...`
- `python main.py live ...`

Those commands map to two implementation surfaces:

### Lightweight Surface

Used for fast local workflows and much of the direct local coverage:

- `src/strategy/`
- `src/backtest/`
- `src/data/`
- `src/risk/`
- `src/execution/`
- `src/broker/`

### Top-Level Runtime Surface

Used for richer runtime operations:

- `adapters/`
- `algorithms/`
- `order_management/`
- `risk_management/`
- `database/`
- `config/`
- `trading_logging/`

## What Is Verified By Default

The current repo and docs are aligned around this verified baseline:

- `python main.py list-strategies`
- `python main.py backtest --help`
- `python main.py init-db --help`
- `python main.py paper --help`
- `python main.py live --help`
- `pytest -q`
- compile and smoke checks from the `Makefile`

This means the repo has a real executable contract. It does not mean every subsystem is equally mature.

## What The Repo Can Do Well Today

### Strategy Iteration

- create lightweight strategies in `src/strategy/`
- list them from the CLI
- backtest them against cached Yahoo Finance market data
- pass extra strategy parameters from the CLI

### Runtime Execution

- build a runtime instance from config
- run polling-based paper/live sessions through the runtime bridge
- execute orders only when explicitly enabled
- route top-level runtime orders through the top-level OMS and risk manager

### Infrastructure

- initialize the database schema from the CLI
- load runtime configuration from environment variables
- run a documented local verification flow

## Current Limits

The biggest thing to understand is that the repo still has duplicated conceptual surfaces:

- a lightweight local surface
- a top-level async runtime surface
- an additional extended adapter toolkit under `src/adapters/`

This is workable, but it means contributors must be deliberate about which surface they touch.

Other current limits:

- live-capital use is not blanket-certified
- not every adapter has equal operational validation
- the top-level runtime strategy set is narrower than the lightweight backtest strategy set
- documentation and code now align much better, but the architecture is still not fully consolidated

## Practical Guidance

If you are new here:

- use `backtest` for fast strategy work
- use `paper` to validate top-level runtime behavior safely
- use `live` only as a guarded progression step
- do not assume `src/strategy` additions automatically become `paper/live` strategies
- do not assume `src/adapters/` and top-level `adapters/` are interchangeable

## Recommended Reading Order

1. [README.md](../README.md)
2. [ARCHITECTURE.md](ARCHITECTURE.md)
3. [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)
4. [ADAPTER_SURFACES.md](ADAPTER_SURFACES.md)
5. [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md)
