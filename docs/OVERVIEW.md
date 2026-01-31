# Architecture Overview

A high-level summary of the algorithmic trading platform's architecture, key decisions, and quick start guide.

---

## Key Architectural Decisions

### 1. Event-Driven Architecture (EDA)

The system uses a **Reactor Pattern** implemented via Python's `asyncio`. Instead of blocking operations, components communicate through events:

- **MarketDataEvent** → Price updates from exchanges
- **SignalEvent** → Trading signals from strategies
- **OrderRequest** → Orders to be executed
- **OrderFillEvent** → Execution confirmations

**Why**: Decouples components, enables real-time processing of multiple data streams without blocking, and supports the 24/7 operational requirement.

### 2. Modular Monolith (vs Microservices)

Components communicate via in-memory references and efficient queues rather than network calls.

**Why**: For a single-node local deployment, microservices introduce unnecessary network serialization overhead (latency) and operational complexity. The modular monolith minimizes "tick-to-trade" latency while maintaining clean separation of concerns.

### 3. Hybrid Concurrency Model

| Layer | Technology | Use Case |
|-------|------------|----------|
| **I/O Operations** | AsyncIO | WebSocket connections, API calls, event routing |
| **CPU-Intensive Work** | Multiprocessing | ML inference, complex calculations, indicator computation |

**Why**: Python's GIL limits true parallelism in threads. By offloading heavy computations to separate processes via `ProcessPoolExecutor`, the main event loop remains responsive to market data (microsecond-scale latency).

---

## Technology Stack Summary

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Core Runtime** | Python 3.10+ + AsyncIO | Event loop, coroutines, non-blocking I/O |
| **Event Loop Optimization** | uvloop | Cython-based event loop (Go/Node.js comparable speed) |
| **Data Validation** | Pydantic | Type-safe event models, configuration parsing |
| **Crypto Exchanges** | CCXT Pro | Unified WebSocket/REST API for 100+ crypto exchanges |
| **Equity/Stock Brokers** | ib_insync | Pythonic wrapper for Interactive Brokers API |
| **Hot Storage** | Redis | In-memory state (LTP, open orders), sub-millisecond access |
| **Cold Storage** | ArcticDB | Time-series database for tick data, backtesting, analysis |
| **Containerization** | Docker + Docker Compose | Reproducible environment, service orchestration |
| **Monitoring** | Prometheus + Grafana | Metrics collection, real-time dashboards |
| **Numerical Computing** | NumPy + Pandas | Vectorized calculations, technical indicators |

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXCHANGE CONNECTIVITY                              │
│  ┌─────────────────────┐        ┌─────────────────────┐                     │
│  │   CCXT Pro          │        │   ib_insync         │                     │
│  │   (Crypto)          │        │   (Equities)        │                     │
│  └──────────┬──────────┘        └──────────┬──────────┘                     │
└─────────────┼──────────────────────────────┼───────────────────────────────┘
              │                              │
              ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GATEWAY LAYER (Adapters)                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  • Symbol normalization    • Rate limiting    • Reconnection logic   │   │
│  │  • Order book management   • WebSocket loop   • Error handling       │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                                     ▼ TickEvent, BarEvent
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EVENT ENGINE (asyncio.Queue)                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Routes events to subscribers: Strategy → Risk → Execution         │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│  STRATEGY       │    │  RISK ENGINE        │    │  EXECUTION          │
│  LAYER          │    │                     │    │  LAYER              │
│                 │    │ • Pre-trade checks  │    │                     │
│ • on_tick()     │    │ • Kill switch       │    │ • Order routing     │
│ • on_bar()      │    │ • Circuit breakers  │    │ • Fill tracking     │
│ • SignalEvent   │───▶│ • Position limits   │───▶│ • Slippage model    │
│                 │    │ • Drawdown limits   │    │                     │
└─────────────────┘    └─────────────────────┘    └─────────────────────┘
                                                             │
                                                             ▼ OrderRequest
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA LAYER                                         │
│                                                                              │
│   ┌─────────────────────┐              ┌─────────────────────┐              │
│   │   HOT STORAGE       │              │   COLD STORAGE      │              │
│   │   Redis             │              │   ArcticDB          │              │
│   │                     │              │                     │              │
│   │ • Last traded price │              │ • Historical ticks  │              │
│   │ • Open orders       │              │ • Backtest data     │              │
│   │ • Session state     │              │ • Time-series       │              │
│   │ • O(1) access       │              │ • Versioning        │              │
│   └─────────────────────┘              └─────────────────────┘              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Steps

1. **Inbound**: Exchange data → Gateway (normalized) → Event Engine → Strategy
2. **Signal Generation**: Strategy emits `SignalEvent` → Risk Engine validates
3. **Execution**: Approved signals become `OrderRequest` → Gateway → Exchange
4. **Persistence**: Ticks cached in Redis (hot) + buffered to ArcticDB (cold)
5. **Monitoring**: All events emit metrics to Prometheus → Grafana dashboard

---

## Risk Management Overview

### Pre-Trade Validators

Every signal passes through these checks before becoming an order:

| Check | Purpose |
|-------|---------|
| **Max Order Value** | Prevents accidentally trading entire account |
| **Price Deviation** | Rejects orders if price > 5% from last known (catches data errors) |
| **Fat Finger Check** | Quantity sanity check against typical trade sizes |
| **Frequency Throttle** | Max 10 orders/minute (prevents infinite loops) |

### Kill Switch (Circuit Breaker)

Monitors Net Liquidation Value (NLV) in real-time:

- **Trigger**: Daily drawdown exceeds threshold (default: 3%)
- **Actions**:
  1. Cancel all working orders
  2. Optionally flatten all positions
  3. Lock system to `HALTED` state
  4. Require manual operator reset

### Post-Trade Risk Metrics

Background thread calculates every minute:
- **Value at Risk (VaR)**
- **Portfolio Exposure** by sector/asset
- **Correlation heatmaps**

---

## Quick Start for Developers

### 1. Project Structure

```
algo-trading/
├── src/                    # Core framework code
│   ├── core/              # Event engine, base classes
│   ├── gateways/          # Exchange adapters (CCXT, IBKR)
│   ├── data/              # Redis & ArcticDB wrappers
│   ├── risk/              # Risk engine, validators
│   └── execution/         # Order routing, OMS
├── strategies/            # Your trading algorithms
│   ├── __init__.py
│   └── my_strategy.py
├── config/                # YAML configurations
│   ├── config.yaml
│   └── logging.yaml
├── docs/                  # Documentation
├── scripts/               # Utilities, maintenance
├── tests/                 # Test suite
├── docker-compose.yml     # Infrastructure services
└── main.py               # Application entry point
```

### 2. Creating a Strategy

```python
from src.core.strategy import BaseStrategy
from src.core.events import TickEvent, SignalEvent

class MyStrategy(BaseStrategy):
    def on_init(self):
        """Called once at startup"""
        self.fast_ma = []
        self.slow_ma = []
    
    def on_tick(self, tick: TickEvent):
        """Called on every price update"""
        self.fast_ma.append(tick.price)
        self.slow_ma.append(tick.price)
        
        if len(self.fast_ma) > 20:
            self.fast_ma.pop(0)
        if len(self.slow_ma) > 50:
            self.slow_ma.pop(0)
        
        # Generate signal (goes to Risk Engine first!)
        if len(self.fast_ma) >= 20 and len(self.slow_ma) >= 50:
            if sum(self.fast_ma)/20 > sum(self.slow_ma)/50:
                self.buy(tick.symbol, quantity=1.0)
            else:
                self.sell(tick.symbol, quantity=1.0)
```

### 3. Running the System

```bash
# 1. Start infrastructure (Redis, Prometheus, Grafana)
docker-compose up -d redis prometheus grafana

# 2. Run backtest
python main.py --mode backtest \
    --strategy my_strategy \
    --symbol BTC/USDT \
    --start 2024-01-01 \
    --end 2024-06-01

# 3. Paper trading
python main.py --mode paper \
    --strategy my_strategy \
    --symbols BTC/USDT,ETH/USDT \
    --exchange binance

# 4. Live trading (caution!)
python main.py --mode live \
    --strategy my_strategy \
    --symbols BTC/USDT \
    --exchange binance \
    --risk-check
```

### 4. Hot Reloading During Development

Modify your strategy code and reload without restarting:

```bash
# In another terminal, trigger reload
curl -X POST http://localhost:8000/api/reload
```

The system will:
1. Serialize strategy state (entry prices, positions, etc.)
2. Reload the Python module
3. Restore state to the new instance
4. Resume trading

### 5. Monitoring

Access the Grafana dashboard at `http://localhost:3000`:

- **Tick-to-Trade Latency**: System responsiveness (target: <10ms)
- **P&L Chart**: Realized/unrealized profit & loss
- **Position Exposure**: Current holdings by asset
- **Gateway Status**: Connection health per exchange
- **Risk Metrics**: Drawdown, VaR, order frequency

---

## Configuration Essentials

### Minimal `config.yaml`

```yaml
# Core system
system:
  mode: paper  # backtest, paper, live
  log_level: INFO

# Risk limits
risk:
  max_drawdown_pct: 3.0
  max_order_value: 1000.0
  daily_loss_limit: 500.0

# Exchange connections
exchanges:
  binance:
    api_key: ${BINANCE_API_KEY}
    api_secret: ${BINANCE_API_SECRET}
    testnet: true
  
  interactive_brokers:
    host: 127.0.0.1
    port: 7497

# Data storage
data:
  redis_url: redis://localhost:6379
  arctic_path: ./data/arctic
```

---

## Key Design Principles

1. **Crash-Only Design**: System can be killed at any moment and restart safely
2. **Redis as Source of Truth**: Latest state always in memory for instant recovery
3. **Write-Ahead Logging**: Ticks appended to WAL before ArcticDB flush (crash recovery)
4. **Event Sourcing**: All state changes via events → reproducible, debuggable
5. **Fail-Safe Defaults**: Missing config → conservative/safe values
6. **Defense in Depth**: Multiple risk checks at strategy, risk engine, and gateway layers

---

## Additional Resources

- **[Full Architecture Specification](./ARCHITECTURE.md)** - Complete technical documentation
- **[API Reference](../AGENTS.md)** - Developer guide for extending the system
- **[Configuration Examples](../config/)** - Sample configs for different exchanges
- **[Docker Setup](../docker-compose.yml)** - Infrastructure orchestration

---

*For detailed implementation of any component, refer to the [full architecture document](./ARCHITECTURE.md).*
