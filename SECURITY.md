# Security Policy

This repository contains trading, execution, authentication, and logging code. Security problems here can affect credentials, live trading behavior, financial controls, and operational safety.

## Reporting A Vulnerability

Please report security issues privately to the repository maintainer instead of opening a public issue with exploit details.

When reporting, include:

- the affected file or subsystem
- clear reproduction steps
- impact assessment
- whether the issue affects local-only, paper, or live execution paths
- any suggested remediation or mitigations

## Security-Sensitive Areas

The most important security and safety boundaries in this repository are:

- exchange authentication and request signing
- risk-gated execution flows
- secrets and environment configuration
- database connection handling
- logging and error reporting
- paper/live runtime behavior
- order submission boundaries

## Current Security Posture

The current codebase includes several hardening measures that are part of the supported path today:

- secret-aware settings types in `config/settings.py`
- safer database URL construction
- explicit CLI guards around live order execution
- risk validation in the lightweight execution path
- risk validation in the top-level OMS path
- explicit logging bootstrap and redaction-oriented runtime guidance

This is meaningful progress, but it is not the same thing as end-to-end operational certification for unattended live trading.

## Core Expectations For Contributors

### Credentials

- never commit real API keys, passphrases, wallet secrets, or private keys
- do not log secret-bearing request payloads or auth material
- prefer secret-aware config handling for any new credential fields
- treat local traceback dumps as potentially sensitive

### Execution Safety

- keep live order execution explicit, never implicit
- preserve or strengthen fail-closed behavior at order submission boundaries
- do not bypass `can_trade()` or equivalent risk approval paths
- treat circuit-breaker semantics as final unless you are intentionally changing them with tests and review

### Logging

- avoid introducing logs that could expose secrets, order payloads, or private-key material
- be careful with exception logging around signing and auth code
- ensure debug-focused diagnostics are not silently treated as production defaults

### Configuration

- prefer environment-driven settings through `config/settings.py`
- avoid adding insecure credential fallbacks
- ensure new connection strings and auth configs are encoded safely

## Scope Notes

This repo contains more code than the main shipped runtime uses directly. Security expectations still apply across the whole repo, but review priority should follow the actual executable surfaces:

1. `main.py` and `src/cli.py`
2. `src/runtime/`
3. top-level `adapters/`, `algorithms/`, `order_management/`, `risk_management/`
4. lightweight `src/` strategy, risk, execution, and backtest surfaces
5. extended `src/adapters/` toolkit and less-central subsystems

## Secure Development Guidelines

### If You Change Exchange Adapters

- validate signing logic carefully
- avoid leaking raw auth inputs in logs or exceptions
- prefer sandbox/testnet validation before claiming readiness
- document whether the adapter is part of the shipped runtime surface or an auxiliary surface

### If You Change Risk Or Execution Code

- add focused tests
- confirm behavior remains fail-closed
- verify both “trading allowed” and “trading blocked” paths
- document user-facing or operator-facing implications

### If You Change Config Or Logging

- confirm secrets are still redacted or secret-aware
- avoid introducing plain-text credential dumps in startup logs
- keep environment variable names and docs aligned

## Operational Reminder

The presence of `paper` and `live` commands does not mean this repository is approved for unattended real-capital deployment.

Live-capital use should be treated as a separate operational-readiness decision requiring:

- venue-specific sandbox validation
- credential hygiene
- runtime monitoring
- operator review of risk settings
- confidence in the exact adapter and strategy path being used

For the current readiness summary, see [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md).
