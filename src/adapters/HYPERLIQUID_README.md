# Hyperliquid Exchange Adapter Implementation

## Overview

A comprehensive exchange adapter for Hyperliquid decentralized perpetual futures exchange has been implemented in `/home/khoinet/repos/development/algo-trading/src/adapters/hyperliquid.py`.

## Features Implemented

### 1. REST API Endpoints

| Method | Description |
|--------|-------------|
| `get_account()` | Get account info and permissions |
| `get_balances()` | Get USDC collateral balances |
| `get_positions()` | Get open perpetual positions with leverage and margin mode |
| `place_order()` | Place market/limit orders with EIP-712 signing |
| `cancel_order()` | Cancel orders by order ID |
| `get_order_status()` | Check order state |
| `get_open_orders()` | Get all open orders |
| `get_ticker()` | Get mark price, index price, volume |
| `get_orderbook()` | Get L2 orderbook |
| `get_historical_candles()` | Get OHLCV data |
| `get_funding_rate()` | Get current and predicted funding rates |

### 2. WebSocket Connections

| Method | Description |
|--------|-------------|
| `subscribe_to_trades()` | Real-time trade updates |
| `subscribe_to_orderbook()` | Orderbook L2 updates |
| `subscribe_to_ticker()` | Mark price updates |
| `subscribe_to_candles()` | Candlestick stream |
| `subscribe_to_user()` | Private account updates (fills, orders) |
| `_ws_loop()` | Auto-reconnect with exponential backoff |

### 3. Hyperliquid-Specific Features

- **EIP-712 Typed Data Signing**: Full Ethereum wallet signature support
- **Arbitrum L2 Integration**: Uses Hyperliquid's Arbitrum deployment
- **Perpetual Futures Trading**: No spot markets (as per Hyperliquid design)
- **Cross & Isolated Margin**: `update_leverage()` with margin mode selection
- **Funding Rate Tracking**: Current and predicted funding rates
- **Liquidation Price Calculation**: `get_liquidation_price()` method
- **Vault API Support**: Optional vault address for delegated trading

### 4. Authentication

- Ethereum wallet private key signing
- EIP-712 order signing for all trading operations
- Session key support via `approve_agent()`
- User-signed actions for transfers/withdrawals

### 5. Additional Methods

- `update_leverage()` - Update leverage for a symbol
- `get_liquidation_price()` - Get liquidation price for position
- `get_user_fees()` - Get fee schedule and volume
- `get_funding_history()` - Historical funding payments
- `approve_agent()` - Create delegated trading agents
- `usd_transfer()` - Transfer USDC on Hyperliquid
- `withdraw()` - Withdraw to Arbitrum

## Usage Example

```python
from src.adapters import ExchangeFactory, Order, OrderSide, OrderType
from decimal import Decimal

# Create adapter
adapter = ExchangeFactory.create(
    exchange="hyperliquid",
    api_key="0xYOUR_WALLET_ADDRESS",
    api_secret="0xYOUR_PRIVATE_KEY",
    sandbox=True  # Use testnet
)

async with adapter:
    # Get account info
    account = await adapter.get_account()
    print(f"Account: {account.account_id}")
    
    # Get balances
    balances = await adapter.get_balances()
    for balance in balances:
        print(f"{balance.asset}: {balance.free} free, {balance.locked} locked")
    
    # Place a limit order
    order = Order(
        symbol="BTC",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.1"),
        price=Decimal("40000"),
        time_in_force=TimeInForce.GTC
    )
    placed = await adapter.place_order(order)
    print(f"Order placed: {placed.order_id}")
    
    # Subscribe to trades
    def on_trade(data):
        print(f"Trade: {data}")
    
    await adapter.subscribe_to_trades("BTC", on_trade)
    
    # Get positions
    positions = await adapter.get_positions()
    for pos in positions:
        print(f"{pos.symbol}: {pos.quantity} @ {pos.avg_entry_price}")
```

## Dependencies

```
eth-account>=0.8.0
msgpack>=1.0.0
cryptography>=3.0.0
httpx>=0.24.0
websockets>=11.0.0
aiolimiter>=1.0.0
```

## Architecture

The adapter follows the existing `BaseExchangeAdapter` interface:

1. **Inheritance**: Extends `BaseExchangeAdapter` for consistent API
2. **EIP-712 Signing**: Custom signing logic for Ethereum wallet authentication
3. **Metadata Caching**: Asset mappings cached for performance
4. **WebSocket Management**: Auto-reconnect with exponential backoff
5. **Error Handling**: Proper exception translation

## File Structure

```
src/adapters/
├── hyperliquid.py         # Main adapter implementation (1586 lines)
├── test_hyperliquid.py    # Unit tests (290 lines)
└── __init__.py            # Auto-registered via @register_adapter
```

## API References

- Hyperliquid API Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- Official Python SDK: https://github.com/hyperliquid-dex/hyperliquid-python-sdk

## Testing

Run tests with pytest:

```bash
cd ~/repos/development/algo-trading
python -m pytest src/adapters/test_hyperliquid.py -v
```

## Notes

1. **No Spot Trading**: Hyperliquid is perpetual futures only
2. **USDC Collateral**: All balances are in USDC
3. **EIP-712 Required**: All trading requires Ethereum wallet signing
4. **Testnet Available**: Use `sandbox=True` for testing
5. **WebSocket Auto-Reconnect**: Built-in with exponential backoff
