# Adapter Surfaces

This repository has two adapter packages:

- top-level `adapters/`
- `src/adapters/`

They are related, but they are not the same thing, and they are not used interchangeably by the current shipped runtime.

## Short Answer

If you want the shortest practical explanation:

- `adapters/` is the adapter surface used by the current `paper` and `live` runtime.
- `src/adapters/` is a broader adapter toolkit with extra infrastructure, utilities, and venue work that is not the main adapter dependency of the shipped runtime today.

## Why This Split Exists

The repo evolved in layers:

- one surface supports the top-level async trading runtime used by the current CLI bridge
- another surface contains extended adapter work such as auth helpers, normalizers, monitoring utilities, and additional venue implementations

This makes the repo flexible, but it also creates confusion for new contributors.

## Top-Level `adapters/`

### What It Is

The top-level `adapters/` package is the adapter layer used by:

- `src/runtime/top_level.py`
- `algorithms/base_algorithm.py`
- `algorithms/sma_crossover.py`
- `order_management/order_manager.py`
- `risk_management/risk_manager.py`

### What It Contains

This surface provides:

- the exchange adapter interface used by the shipped runtime
- order and market domain models used by the top-level runtime
- the exchange implementations wired into the current CLI runtime

### When To Work Here

Work in `adapters/` when:

- you are adding or changing exchange support for `paper/live`
- you are fixing the adapter behavior used by the top-level OMS
- you are working on runtime execution paths that already import from `adapters/`

### Current Shipped Runtime Dependence

The current runtime bridge builds adapters from this package for:

- Binance
- Kraken
- Coinbase

## `src/adapters/`

### What It Is

`src/adapters/` is an extended adapter toolkit.

It includes:

- a richer base adapter and auth/signing stack
- normalizer utilities
- health monitoring helpers
- testnet validation helpers
- additional venue implementations such as Bybit and Hyperliquid

### What It Is Not

It is not the main adapter package imported by the current shipped `paper/live` runtime bridge.

That does not make it unimportant. It means its role is different.

### When To Work Here

Work in `src/adapters/` when:

- you are improving the shared adapter toolkit in that subsystem
- you are working on auth/signing primitives
- you are working on extended venue adapters that live there
- you are touching tests or utilities that explicitly import from `src.adapters`

## Practical Decision Guide

### “I want my exchange change to affect `python main.py paper/live`.”

Start with `adapters/`.

### “I am working on Hyperliquid, Bybit, normalizers, auth helpers, or adapter health tooling.”

Start with `src/adapters/`.

### “I want to add a brand-new exchange to the currently shipped runtime.”

You likely need:

- a top-level adapter in `adapters/`
- registration and runtime bridge alignment
- tests and documentation updates

### “I found similar models in both packages.”

Yes. That duplication is real, and it is one of the repo’s main maintenance costs.

## Risks Of Confusing The Two

If you edit the wrong surface, you can easily end up with:

- a change that does not affect the runtime you meant to change
- docs that overclaim support
- tests passing in one subsystem while the shipped runtime remains unchanged
- duplicated fixes in multiple places

## Recommended Contributor Behavior

Before editing adapter code:

1. Search for the exact import path used by the code you are changing.
2. Confirm whether the shipped runtime depends on that package.
3. Update docs to reflect the surface you actually changed.
4. Avoid creating a third adapter abstraction.

## Current Recommendation

For day-to-day work:

- treat `adapters/` as authoritative for the current shipped runtime
- treat `src/adapters/` as an auxiliary but important extended subsystem
- prefer consolidation over further divergence when you have a clear, safe path to do so

## Related Docs

- [README.md](../README.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)
- [src/adapters/HYPERLIQUID_README.md](../src/adapters/HYPERLIQUID_README.md)
