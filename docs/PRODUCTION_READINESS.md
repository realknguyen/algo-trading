# Production Readiness

This repository is materially stronger than a prototype, but it should still be treated as a hardening baseline rather than blanket authorization for unattended live-capital deployment.

## Bottom Line First

What is true today:

- the CLI is real and coherent
- the runtime bridge is executable
- order execution is guarded rather than implicit
- config and settings handling are meaningfully hardened
- risk checks exist in both the lightweight and top-level execution paths

What is not true today:

- every adapter is fully venue-certified for unattended real-capital use
- the whole repo is architecturally consolidated into one runtime surface
- command availability alone guarantees operational readiness

## Areas That Are In Better Shape Now

### Entry Surface

- one canonical CLI entrypoint
- documentation now aligned with the shipped command surface
- `paper` and `live` are executable and documented

### Configuration And Secrets

- secret-aware settings are used in the main config layer
- database URL construction is safer than raw string interpolation
- runtime behavior is driven through a central settings module

### Execution Safety

- live order execution requires explicit intent
- dry-run remains the default for `paper` and `live`
- top-level order submission goes through risk-aware runtime components
- lightweight execution also enforces risk checks

### Developer Ergonomics

- the `Makefile` gives a clear local verification path
- tests cover the main CLI/runtime bridge and strategy/backtest flow
- contributor guidance is present and aligned to the code

## What Is Supported Today

The repo currently supports these use cases with the highest confidence:

- local strategy discovery
- local backtesting through `python main.py backtest ...`
- database schema initialization through `python main.py init-db`
- guarded paper/live polling through `python main.py paper ...` and `python main.py live ...`
- targeted strategy, CLI, runtime bridge, risk, execution, and settings verification through the current test suite

## Important Limits

### Architectural Limits

- the repo still has multiple overlapping implementation surfaces
- the adapter story is split between `adapters/` and `src/adapters/`
- the lightweight and top-level runtime strategy surfaces are not fully unified

### Operational Limits

- no blanket claim of venue certification for all adapters
- no blanket claim of unattended live-readiness
- broader runtime and adapter code exists outside the most central default test path

### Validation Limits

- database bootstrap is covered, but not every production-style DB lifecycle concern is part of the default suite
- exchange-specific sandbox verification is still venue-dependent
- non-default adapter surfaces are less central to the shipped CLI contract

## Readiness By Use Case

### Good Fit Right Now

- learning the codebase
- building and comparing strategies locally
- backtesting using the lightweight stack
- validating runtime wiring and command behavior
- paper-mode or dry-run progression work

### Use With Caution

- adapter-specific live execution changes
- venue-specific auth/signing changes
- anything involving protected risk logic
- claims that a new adapter is “production-ready” without sandbox evidence

### Not Something The Repo Should Promise By Default

- unattended real-capital operation across all supported exchanges
- full operational certification of extended adapter surfaces
- full architectural unification

## Recommended Path Before Real-Capital Use

If you intend to operate with real capital, the minimum responsible path is:

1. verify the strategy logic in the lightweight backtest path
2. verify the runtime strategy path in `paper` dry-run mode
3. verify execution-enabled behavior in safe/sandbox conditions
4. validate the exact exchange adapter and symbols you will use
5. review risk settings and operational monitoring expectations
6. only then consider guarded live execution

## Highest-Value Next Milestones

1. Consolidate shared domain concepts across the duplicated runtime surfaces.
2. Clarify the long-term plan for `adapters/` versus `src/adapters/`.
3. Add more venue-specific sandbox verification for the adapters intended for live use.
4. Keep runtime-support claims narrow and evidence-based.

## Practical Interpretation

This repo has moved beyond “toy project” territory. It has:

- a real executable contract
- meaningful safety guards
- a contributor workflow
- documentation that reflects the code

That is a strong baseline.

The correct framing, though, is:

- engineering-ready for continued hardening
- useful for local strategy and guarded runtime work
- not automatically deployment-certified for unattended live trading
