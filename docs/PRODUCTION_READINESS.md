# Production Readiness

This repository is materially closer to production engineering standards than it was before the recent hardening work, but it is still not a blanket "ready for unattended live capital" system.

## Stronger Now

- Explicit logging bootstrap with secret redaction
- Fail-closed risk checks on the lightweight execution path and legacy OMS path
- Secret-aware settings and safer database URL construction
- Stable developer workflow via CI, `Makefile`, and pre-commit configuration
- Documentation aligned to the current shipped CLI instead of outdated aspirational flows
- Executable `init-db`, `paper`, and `live` command surfaces

## Supported Today

- Local strategy discovery
- Local backtesting through `python main.py backtest ...`
- Database schema initialization through `python main.py init-db`
- Guarded paper/live polling through `python main.py paper ...` and `python main.py live ...`
- Test-driven validation of the lightweight strategy, risk, execution, broker, and backtest path

## Not Yet Production-Complete

- No end-to-end operational certification of every venue adapter
- No claim that guarded live execution is venue-certified for unattended capital deployment
- No single unified runtime surface across `src/` and the top-level async packages
- Limited database-path smoke coverage in the default local suite

## Recommended Next Milestones

1. Choose one public runtime surface and deprecate the other.
2. Add database migration/init verification to the default CI path.
3. Add sandbox-backed adapter checks for the venues you plan to operate.
4. Add release/build artifact validation if this repo will publish installable artifacts regularly.

## Bottom Line

This repo now has a much cleaner executable contract, better security defaults, and better contributor ergonomics. It should be treated as a solid engineering baseline for further hardening, not as a blanket authorization to trade live funds unattended.
