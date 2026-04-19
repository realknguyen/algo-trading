# Contributing

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Local Commands

```bash
make compile
make smoke
make test
make verify
```

- `make compile` checks import/bytecode health across the main runtime packages.
- `make smoke` verifies the shipped CLI contract, including runtime help and database-init smoke coverage.
- `make verify` runs compile checks, CLI smoke checks, and the default test suite.

## Working Rules

- Keep changes aligned with the current executable path in `main.py` and `src/cli.py`.
- Prefer simple, production-friendly fixes over broad rewrites.
- Do not change `risk_management/` behavior casually. Treat it as protected logic.
- Keep documentation in sync with the shipped CLI and tested runtime surface.

## Pull Requests

- Include the smallest relevant verification output in the PR description.
- Call out behavior changes, especially around execution, risk, logging, or config.
- Avoid documenting features as supported unless they are wired into the current entrypoint and tested.

## Style and Tooling

- Run `pre-commit install` after setup if you want local hooks.
- Formatting is handled with `black`.
- Linting is handled with `flake8`.
- Type checks currently focus on the shipped CLI/runtime bridge surface: `src/cli.py`, `src/data`, `src/runtime`, `config`, and `trading_logging`.
