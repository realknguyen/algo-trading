# Hyperliquid Adapter Notes

This document explains where the Hyperliquid adapter lives, what part of the repo it belongs to, and how to work on it without confusing it with the main shipped runtime adapter surface.

## Location

Primary implementation:

- `src/adapters/hyperliquid.py`

Primary dedicated test module:

- `src/adapters/test_hyperliquid.py`

## Which Adapter Surface It Belongs To

The Hyperliquid adapter belongs to the extended `src/adapters/` surface.

That means:

- it is part of the broader adapter toolkit in `src/adapters/`
- it is not part of the top-level `adapters/` package used by the current shipped `paper/live` runtime bridge
- changes here do not automatically affect the adapter behavior used by the main runtime commands

If you are new to the repo, read:

- [../../docs/ADAPTER_SURFACES.md](../../docs/ADAPTER_SURFACES.md)

## Intended Scope

This adapter is designed to cover Hyperliquid-specific functionality such as:

- account and balance queries
- perpetual position queries
- order placement and cancellation
- ticker, orderbook, and candle retrieval
- websocket subscriptions with reconnect handling

This is a venue-specific adapter surface, not a generic spot-exchange drop-in.

## Current Status

Practical status summary:

- important and useful adapter code
- lives outside the primary shipped runtime adapter boundary
- should be treated as needing targeted verification when worked on
- should not be described as part of the default `paper/live` runtime support unless that wiring is added explicitly

## Testing And Verification

This adapter is not part of the default `pytest -q` root test path in the same central way as the main shipped CLI/runtime surface.

If you are changing this adapter, run its dedicated tests directly:

```bash
python -m pytest src/adapters/test_hyperliquid.py -v
```

You should also consider targeted manual validation for:

- authentication and signing
- market-data normalization
- websocket reconnect behavior
- order lifecycle semantics

## Contributor Guidance

When changing this adapter:

- do not assume the top-level `adapters/` package will pick up your change
- do not document Hyperliquid as part of the shipped runtime unless the runtime bridge is updated
- be especially careful with secrets, signing logic, and logging behavior

## Dependency Notes

This adapter depends on the broader project runtime stack, including:

- async HTTP infrastructure
- websocket-related dependencies
- signing/auth helpers
- cryptography-related project dependencies

If you are validating this adapter in isolation, install the full project dependencies first.
