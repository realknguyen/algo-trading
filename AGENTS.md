# AGENTS.md - Trading System Workspace

## System Overview
This repository contains a high-performance algorithmic trading platform. As an agent, you are responsible for maintaining strategy integrity and ensuring safe execution.

## Core Directives

### 1. Safety First
- **NEVER** modify `risk_management/` logic without explicit human approval.
- **ALWAYS** verify `can_trade()` status before suggesting order execution.
- **CIRCUIT BREAKERS** are final. If they trigger, analyze the root cause before requesting a reset.

### 2. Strategy Development
- When implementing new strategies, use `algorithms/base_algorithm.py` as the template.
- All new strategies **MUST** include a corresponding test in `tests/`.
- Prefer `QuantConnectAdapter` for strategies ported from the QC ecosystem.

### 3. Database Integrity
- Use SQLAlchemy ORM for all database interactions.
- Ensure all trades and orders are logged to the `orders` and `trades` tables.
- Large data imports should be handled via the `DataFetcher` to utilize local caching.

## Maintenance Procedures

### Adding an Exchange
1. Create a new file in `adapters/`.
2. Inherit from `BaseExchangeAdapter`.
3. Implement `_authenticate`, `_sign_request`, and all abstract market/order methods.
4. Register the new adapter in `adapters/__init__.py`.

### Updating Config
- Add new settings to `config/settings.py` using Pydantic fields.
- Ensure appropriate env prefixes are used (`BINANCE_`, `RISK_`, etc.).

## Troubleshooting
- Check `logs/trading.log` for structured events.
- Audit `system_logs` table in PostgreSQL for persistent error history.
- Use `python main.py init-db` to repair or initialize the schema.

## Implementation Reference

### Exchange Adapters Summary
| Name | File | Status | Core Lines | Features |
|------|------|--------|------------|----------|
| **Binance** | `adapters/binance.py` | COMPLETE | ~400 | Spot, WS, User Streams |
| **Bybit** | `src/adapters/bybit.py` | COMPLETE | ~1,600 | Unified V5 API |
| **Kraken** | `adapters/kraken.py` | COMPLETE | ~1,400 | REST/WS Hybrid |
| **Coinbase** | `adapters/coinbase.py` | COMPLETE | ~1,200 | Adv Trade API |
| **Hyperliquid** | `src/adapters/hyperliquid.py` | COMPLETE | ~1,600 | L2 Book Reconstruction |
| **Base** | `src/adapters/base_adapter.py` | COMPLETE | ~300 | Abstract Foundation |

### API Client Components
- **Rate Limiter**: `src/rate_limiter/rate_limiter.py` - Token bucket implementation.
- **HTTP Client**: `src/rate_limiter/http_client.py` - Httpx-based async client with retries.
- **Normalizer**: `src/adapters/normalizer.py` - Unified Pydantic models for all exchanges.
- **Tracing**: `src/tracing/` - Distributed context propagation and request logging.
- **Metrics**: `src/metrics/` - Prometheus metric collection and alerting.

### How to Use Implemented Features

#### 1. Unified API Access
All adapters follow the same interface. Switching exchanges is as simple as changing the class:
```python
from adapters.binance import BinanceAdapter
from adapters.kraken import KrakenAdapter

# Switchable adapters
adapter = BinanceAdapter(key, secret) # or KrakenAdapter(key, secret)
ticker = await adapter.get_ticker("BTCUSDT")
```

#### 2. Risk-Aware Execution
Use the `RiskManager` before executing any orders:
```python
from risk_management.risk_manager import RiskManager
from order_management.order_manager import OrderManager

risk = RiskManager()
if risk.validate_order(order):
    await oms.submit_order(order)
```

### File Structure Overview
```text
├── adapters/            # Core exchange implementations
├── src/
│   ├── adapters/        # Enhanced/New adapters and support logic
│   ├── rate_limiter/    # API client and throttling
│   ├── metrics/         # Prometheus monitoring
│   ├── tracing/         # OpenTelemetry tracing
│   └── execution/       # Order execution logic
├── database/            # SQLA models and migrations
├── order_management/    # OMS core
└── risk_management/     # Risk rules and sizing
```

For a granular breakdown of all 80 tasks and their current implementation status, see [docs/TASKS.md](docs/TASKS.md).

---
*Current Version: 1.1.0 (Documented Infrastructure)*
