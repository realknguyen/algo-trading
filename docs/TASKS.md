# Task Tracker

## Recently Completed

| Area | Status | Notes |
|---|---|---|
| Runtime import hygiene | COMPLETE | Removed the `logging` namespace collision and moved runtime logging to `trading_logging/` |
| CLI packaging | COMPLETE | Fixed the console entry point to `main:main` |
| Package manifest scope | COMPLETE | `pyproject.toml` now packages the runtime modules that are actually used |
| Lightweight broker coverage | COMPLETE | Added `src/broker/alpaca.py` and `src/broker/binance.py` |
| Lightweight risk-aware strategy | COMPLETE | Added `src/strategy/sma_crossover_risk.py` |
| Lightweight risk manager parity | COMPLETE | Expanded `src/risk/manager.py` to match the current test surface |
| Lightweight execution hardening | COMPLETE | `src/execution/engine.py` now handles broker status fallbacks more safely |
| Lightweight backtest consistency | COMPLETE | `src/backtest/runner.py` now keeps final equity and metrics consistent |
| Docs refresh | COMPLETE | README and `docs/` now describe the repository as it exists today |

## Verified Components

| Component | Status | Verification |
|---|---|---|
| Top-level runtime imports | VERIFIED | Local import smoke test |
| Test suite | VERIFIED | `pytest -q` |
| Top-level CLI wiring | VERIFIED | Entry point and parser checked locally |
| Lightweight `src/` strategy/risk/execution/backtest path | VERIFIED | Unit and integration coverage |

## Open High-Priority Work

| Priority | Task | Why It Matters |
|---|---|---|
| P1 | Consolidate the top-level runtime and `src/` compatibility layer | The duplicate architecture is still the main long-term maintenance risk |
| P1 | Add CI for `pytest -q` and import smoke checks | Prevents regression of the import and packaging issues that were just fixed |
| P1 | Add sandbox-backed adapter verification | Adapter existence is not the same as exchange validation |
| P2 | Finish or formally scope down `paper` mode | The current loop is still a scaffold |
| P2 | Finish or formally remove `live` mode | Avoids implying support that does not exist |
| P2 | Add database migration/init smoke tests | The database path is wired, but not fully exercised in the default suite |

## Deferred / Backlog

| Priority | Task | Notes |
|---|---|---|
| P3 | Broader strategy library expansion | Reasonable after runtime consolidation |
| P3 | More realistic execution simulation in backtests | Useful once the canonical architecture is chosen |
| P3 | Coverage reporting in CI | Helpful, but less urgent than basic green CI |
