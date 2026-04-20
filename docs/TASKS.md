# Task Tracker

## Recently Completed

| Area | Status | Notes |
|---|---|---|
| Risk-gated execution hardening | COMPLETE | Lightweight execution and legacy OMS paths now fail closed |
| Logging hardening | COMPLETE | Canonical logging is explicit and redacts secret-like data |
| Config hardening | COMPLETE | Secret-aware settings and safer DB URL construction are in place |
| CLI/runtime contract cleanup | COMPLETE | Docs and CLI now match each other again |
| Database init surface | COMPLETE | `main.py init-db` now creates schema from the shipped CLI |
| Top-level runtime bridge | COMPLETE | `paper` and `live` now bridge the CLI into the async adapter/algorithm/OMS stack |
| CI smoke coverage | COMPLETE | CI now checks database init and runtime command help surfaces |
| Developer workflow scaffolding | COMPLETE | `Makefile`, `pre-commit`, contributing guidance, and security guidance were added |

## Verified Components

| Component | Status | Verification |
|---|---|---|
| Default test suite | VERIFIED | `pytest -q` |
| CLI registration | VERIFIED | `python main.py list-strategies` |
| Database init | VERIFIED | CLI help + runtime unit coverage |
| Runtime bridge | VERIFIED | Unit coverage for dry-run and execution-enabled paths |
| Broader import/compile health | VERIFIED | `python -m compileall ...` |

## Open High-Priority Work

| Priority | Task | Why It Matters |
|---|---|---|
| P1 | Consolidate shared domain models across `src/` and the top-level async stack | Surface wiring is better, but model duplication still costs maintenance |
| P1 | Add adapter-specific sandbox verification suites for operational venues | Public command availability is not the same as venue certification |
| P2 | Expand type coverage deeper into adapters and order-management modules | Helps safer refactors and clearer contracts |
| P2 | Add release/build artifact smoke tests if package publishing becomes part of the workflow | Useful once packaging distribution is a first-class release path |

## Deferred / Backlog

| Priority | Task | Notes |
|---|---|---|
| P3 | Broader strategy library expansion on the top-level async runtime | Easier after more model consolidation |
| P3 | Replace remaining direct `logger.logger` usage with cleaner wrapper methods | Good cleanup, but not blocking |
