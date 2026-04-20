# Task Tracker

This tracker reflects the current state of the repository at a high level. It is not meant to be a granular issue tracker for every file. It is meant to show what is complete, what is actively stable, and where the biggest structural opportunities still are.

## Completed Foundations

| Area | Status | Notes |
|---|---|---|
| Canonical CLI surface | COMPLETE | `main.py` and `src/cli.py` expose the supported entrypoint |
| Lightweight strategy/backtest flow | COMPLETE | `list-strategies` and `backtest` are wired and documented |
| Database init command | COMPLETE | `init-db` creates schema through the shipped CLI |
| Runtime bridge | COMPLETE | `paper` and `live` bridge into the top-level async runtime |
| Live execution guardrails | COMPLETE | live execution requires explicit CLI intent |
| Config hardening | COMPLETE | secret-aware settings and safer DB URL construction are in place |
| Risk-aware execution boundaries | COMPLETE | lightweight and top-level order submission paths enforce risk checks |
| Developer workflow scaffolding | COMPLETE | `Makefile`, contributor guidance, and smoke paths are in place |
| Documentation alignment | COMPLETE | core docs now describe the actual executable surface |

## Stable Verified Components

| Component | Status | Verification Path |
|---|---|---|
| CLI registration | VERIFIED | `python main.py list-strategies` |
| Backtest CLI surface | VERIFIED | `python main.py backtest --help` plus tests |
| Database init CLI surface | VERIFIED | `python main.py init-db --help` plus runtime DB coverage |
| Runtime help surfaces | VERIFIED | `python main.py paper --help` and `python main.py live --help` |
| Default test suite | VERIFIED | `pytest -q` |
| Compile/import health | VERIFIED | `make compile` |
| CLI smoke workflow | VERIFIED | `make smoke` |

## Current Architectural Realities

These are not necessarily bugs, but they are the main structural facts contributors must work with today:

| Area | Current State | Practical Impact |
|---|---|---|
| Lightweight vs top-level runtime | Both exist and are actively relevant | Contributors must choose the right surface before editing |
| `adapters/` vs `src/adapters/` | Both exist and serve different purposes | Adapter work requires extra clarity and documentation |
| Strategy surfaces | Lightweight registry is broader than shipped runtime strategy support | Backtest support does not automatically imply `paper/live` support |
| Risk surfaces | Lightweight and top-level risk implementations both exist | Changes must preserve semantic consistency |

## High-Value Open Work

### P1

- Clarify and gradually reduce duplication between runtime surfaces where it is safe and worthwhile.
- Establish a clearer long-term ownership model for `adapters/` versus `src/adapters/`.
- Add venue-specific sandbox validation for the adapters intended for real runtime use.

### P2

- Expand typed/tested confidence deeper into adapters and order-management modules.
- Broaden runtime strategy coverage if the shipped `paper/live` surface needs more than `sma_crossover`.
- Improve contributor-facing guidance around strategy promotion from lightweight backtest to runtime execution.

### P3

- Clean up remaining stylistic inconsistencies across logging and helper layers.
- Consolidate duplicated domain models where the migration path is clear and low risk.
- Expand auxiliary adapter documentation for less central venue surfaces.

## Recommended Priorities For Future Work

If maintainers want the best return on effort, the recommended order is:

1. strengthen the shipped runtime surface
2. reduce conceptual duplication
3. improve adapter validation for real operational use
4. only then broaden supported runtime claims

## Notes For Contributors

- A feature is not truly “supported” just because code exists somewhere in the repo.
- Treat the CLI entrypoint and its verified dependencies as the source of truth.
- When updating behavior, update docs in the same change.
