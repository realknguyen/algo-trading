# Contributing

This repository moves fastest when contributors stay aligned with the shipped CLI and the code paths that are actually exercised today.

The main rule is simple:

- prefer the smallest production-friendly change that improves the current supported surface

## Before You Start

Read these first:

- `AGENTS.md` at the repository root
- [README.md](README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md)

That context matters because this repo has two implementation layers:

- a lightweight `src/` layer for local iteration and backtesting
- a top-level async runtime layer used by `paper`, `live`, and `init-db`

When adding or changing behavior, make sure you know which surface you are touching.

## Setup

### Recommended Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

### Python Version

The project metadata targets Python 3.10+ and CI expectations mention Python 3.10 and 3.11.

## Working Principles

### Keep Changes Aligned With The Shipped Entry Point

The canonical user entrypoint is:

```text
main.py -> src/cli.py
```

That means:

- avoid adding undocumented side-entry flows unless there is a strong reason
- avoid describing features as supported unless they are reachable from the CLI or clearly scoped as auxiliary
- prefer strengthening existing paths over introducing parallel ones

### Prefer Simple, Maintainable Solutions

- avoid broad rewrites for small problems
- keep APIs small and explicit
- do not introduce new heavy abstractions unless they materially reduce complexity
- preserve existing runtime expectations unless you are intentionally changing them

### Treat Risk Logic As Protected

`risk_management/` is protected behavior. Do not modify it casually.

If you need to change top-level risk behavior:

- understand the current runtime flow first
- review nearby tests
- add focused verification
- call out the behavioral impact clearly in your change summary or PR

### Keep Docs And Runtime In Sync

If you change:

- CLI arguments
- supported strategies
- supported exchanges
- execution/risk behavior
- database initialization behavior

then update the docs in the same change.

## Repository Surfaces

### Lightweight Local Surface

Use this for:

- strategy iteration
- local backtesting
- lightweight risk/execution tests

Main packages:

- `src/strategy/`
- `src/backtest/`
- `src/data/`
- `src/risk/`
- `src/execution/`
- `src/broker/`

### Top-Level Runtime Surface

Use this for:

- `paper`
- `live`
- `init-db`
- top-level exchange/runtime integration

Main packages:

- `adapters/`
- `algorithms/`
- `order_management/`
- `risk_management/`
- `database/`
- `config/`
- `trading_logging/`

### Extended Adapter Toolkit

`src/adapters/` is an extended adapter surface with richer adapter infrastructure and venue-specific work. It is not the primary adapter surface used by the shipped `paper/live` runtime today.

See [docs/ADAPTER_SURFACES.md](docs/ADAPTER_SURFACES.md) before making adapter changes.

## Common Local Commands

```bash
make compile
make smoke
make test
make verify
```

### Command Reference

- `make compile`
  Checks import and bytecode health across the main packages.

- `make smoke`
  Verifies the shipped CLI contract:
  - `list-strategies`
  - `backtest --help`
  - `init-db` against SQLite
  - `paper --help`
  - `live --help`

- `make test`
  Runs `pytest -q`.

- `make verify`
  Runs compile checks, smoke checks, and the default test suite.

Useful narrower commands:

```bash
pytest -q tests/unit
pytest -q tests/integration
pytest -q tests/unit/test_cli.py
pytest -q tests/unit/test_top_level_runtime.py
```

## How To Approach Common Changes

### Adding A Lightweight Strategy

1. Create the strategy in `src/strategy/`.
2. Register it in `src/strategy/__init__.py`.
3. Add unit tests in `tests/unit/`.
4. Add integration/backtest coverage where appropriate.
5. Verify with:

```bash
python main.py list-strategies
python main.py backtest --strategy your_strategy --symbol BTCUSDT --period 3mo
```

### Adding A Strategy For `paper/live`

1. Implement the top-level runtime strategy under `algorithms/`.
2. Wire it into `src/runtime/top_level.py`.
3. Add runtime-focused tests.
4. Validate in dry-run mode before enabling order execution.

Recommended progression:

```bash
python main.py paper --strategy your_strategy --exchange binance --symbols BTCUSDT --iterations 1
python main.py paper --strategy your_strategy --exchange binance --symbols BTCUSDT --execute-orders --iterations 1
python main.py live --strategy your_strategy --exchange binance --symbols BTCUSDT --iterations 1
```

Only enable live order execution intentionally:

```bash
python main.py live --strategy your_strategy --exchange binance --symbols BTCUSDT --execute-orders --confirm-live --iterations 1
```

### Adding Or Updating An Exchange Adapter

Before you change anything, confirm which surface you mean:

- top-level `adapters/` for shipped runtime support
- `src/adapters/` for extended adapter toolkit work

Do not assume both are interchangeable.

### Changing Config Or Runtime Behavior

- update `config/settings.py` if the setting is part of the shipped runtime
- prefer environment-driven config over ad hoc local config parsing
- keep docs current
- add or update tests near the CLI/runtime bridge when changing runtime semantics

## Pull Request Guidance

### What To Include

- a short statement of what changed
- the user-facing or operator-facing impact
- the smallest relevant verification you ran
- any areas you intentionally did not verify

Good examples:

- “Added `foo_strategy` to the lightweight registry and verified with `python main.py list-strategies` plus targeted unit tests.”
- “Updated runtime argument validation and ran `pytest -q tests/unit/test_cli.py tests/unit/test_top_level_runtime.py`.”

### What To Call Out Explicitly

Always call out changes involving:

- order execution
- risk logic
- live/paper runtime behavior
- credentials or auth/signing
- database initialization or schema behavior
- adapter support claims

### Documentation Expectations

Do not merge a behavior change with stale docs.

At minimum, update whichever of these is affected:

- `README.md`
- `docs/OVERVIEW.md`
- `docs/ARCHITECTURE.md`
- `docs/DEVELOPMENT_WORKFLOW.md`
- `docs/PRODUCTION_READINESS.md`
- `SECURITY.md`

## Style And Tooling

- formatting: `black`
- linting: `flake8`
- type checking: `mypy`
- hooks: `pre-commit`

Current typed/linted focus is strongest around the shipped CLI/runtime bridge surface:

- `src/cli.py`
- `src/data`
- `src/runtime`
- `config`
- `trading_logging`

That means deeper parts of the repo may be less uniformly typed; avoid overclaiming safety based only on the current typecheck scope.

## Review Checklist

Before you wrap up, sanity-check:

- does the change touch the right implementation surface?
- is the supported CLI still correct?
- are risk-sensitive changes fail-closed?
- are docs still accurate?
- did you run the smallest relevant verification and record it clearly?
