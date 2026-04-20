# Security Policy

## Reporting a Vulnerability

Please report security issues privately to the repository maintainer instead of opening a public issue with exploit details.

Include:

- affected files or subsystem
- reproduction steps
- impact assessment
- any suggested remediation or mitigation

## Scope

Security-sensitive areas in this repository include:

- exchange authentication and request signing
- risk-gated execution flows
- secrets and environment configuration
- database connection handling
- logging and error reporting
- live/paper runtime command execution

## Secure Development Expectations

- Never commit real credentials, API keys, or private keys.
- Use sandbox or paper environments when validating adapter behavior.
- Keep risk checks fail closed on any execution path.
- Prefer secret-aware config types and redacted logging for any new sensitive fields.
- Keep live order execution explicitly guarded, never implicit by default.

## Current Baseline

Recent hardening work improved:

- risk enforcement before order submission
- secret redaction in logging
- secret-aware settings handling
- database URL construction

This does not mean the repository is approved for unattended live trading. Treat live-capital use as a separate operational readiness decision.
