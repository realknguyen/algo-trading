# Hyperliquid Adapter Notes

## Location

The Hyperliquid adapter lives at:

- `src/adapters/hyperliquid.py`

Its local test module lives at:

- `src/adapters/test_hyperliquid.py`

## Scope

This adapter belongs to the extended `src/adapters/` surface, not the top-level `adapters/` package used by the main CLI runtime.

It is intended to cover:

- account and balance queries
- perpetual position queries
- order placement and cancellation
- ticker, orderbook, and candle retrieval
- websocket subscriptions with reconnect handling

## Important Notes

- Hyperliquid is perpetual-focused, not a general spot venue.
- This adapter is not part of the default `tests/` collection path, so it is not exercised by `pytest -q` at the repository root.
- Treat it as adapter code that still benefits from targeted sandbox/manual verification before operational use.

## Manual Verification

Run the dedicated tests directly if you are working on this adapter:

```bash
python -m pytest src/adapters/test_hyperliquid.py -v
```

## Dependency Reminder

The adapter depends on the broader runtime HTTP, websocket, signing, and cryptography stack defined in the project dependency manifests. If you are validating this adapter in isolation, install the project dependencies first.
