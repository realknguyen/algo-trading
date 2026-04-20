"""Tests for the top-level runtime bridge."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from adapters.base_adapter import Order, OrderSide, OrderStatus, OrderType
from algorithms.base_algorithm import Signal
from log_config import TradingLogger
from order_management.order_manager import OrderManager, OrderRequest
from risk_management.risk_manager import RiskLimits, RiskManager
from src.runtime.top_level import (
    PollingTradingRuntime,
    _coerce_float_param,
    _coerce_int_param,
)


def _sample_candles() -> list[dict[str, object]]:
    candles: list[dict[str, object]] = []
    base_timestamp = 1_700_000_000_000
    for offset in range(30):
        price = Decimal("100") + Decimal(offset)
        candles.append(
            {
                "timestamp": base_timestamp + offset * 60_000,
                "open": price,
                "high": price + Decimal("1"),
                "low": price - Decimal("1"),
                "close": price,
                "volume": Decimal("10"),
            }
        )
    return candles


class FakeAdapter:
    def __init__(self):
        self.connected = False
        self.placed_orders: list[Order] = []
        self._orders: dict[str, Order] = {}

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_historical_candles(self, symbol: str, interval: str, limit: int = 500, **_kwargs):
        assert interval == "1h"
        return _sample_candles()[-limit:]

    async def place_order(self, order: Order) -> Order:
        filled = replace(
            order,
            order_id=f"order-{len(self.placed_orders) + 1}",
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            avg_fill_price=order.price or Decimal("129"),
        )
        self.placed_orders.append(filled)
        self._orders[filled.order_id or ""] = filled
        return filled

    async def get_order(self, symbol: str, order_id: str) -> Order:
        assert symbol == "BTCUSDT"
        return self._orders[order_id]

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        assert symbol == "BTCUSDT"
        return order_id in self._orders


class FakeAlgorithm:
    def __init__(self, symbols: list[str], order_manager: OrderManager | None = None):
        self.config = SimpleNamespace(name="FakeStrategy", symbols=symbols)
        self.logger = TradingLogger("FakeAlgorithm")
        self.order_manager = order_manager
        self.current_prices: dict[str, Decimal] = {}
        self.data = {}
        self.signal_count = 0
        self.signals: list[Signal] = []
        self.initialized = False
        self.started = False

    def initialize(self, data):
        self.initialized = True
        self.data = data

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False

    def on_data(self, data):
        price = Decimal(str(data["BTCUSDT"]["close"].iloc[-1]))
        return Signal(
            symbol="BTCUSDT",
            action="buy",
            timestamp=datetime.now(),
            price=price,
            confidence=0.95,
            metadata={"source": "test"},
            order_type=OrderType.MARKET,
        )

    async def on_execute(self, signal: Signal):
        if self.order_manager is None:
            return None
        request = OrderRequest(
            symbol=signal.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1"),
            reference_price=signal.price,
        )
        return await self.order_manager.submit_order(request)


@pytest.mark.asyncio
async def test_runtime_runs_in_dry_run_mode_without_connecting():
    """Signal-only runtime mode should not require exchange auth or order execution."""
    adapter = FakeAdapter()
    algorithm = FakeAlgorithm(symbols=["BTCUSDT"])
    runtime = PollingTradingRuntime(
        mode="paper",
        exchange_name="binance",
        interval="1h",
        symbols=["BTCUSDT"],
        adapter=adapter,
        algorithm=algorithm,
        order_manager=None,
        execute_orders=False,
    )

    summary = await runtime.run(iterations=2, poll_seconds=0, lookback=20)

    assert summary.dry_run is True
    assert summary.signals_generated == 2
    assert summary.orders_executed == 0
    assert adapter.connected is False
    assert algorithm.initialized is True


@pytest.mark.asyncio
async def test_runtime_executes_orders_when_enabled():
    """When execution is enabled, the runtime should route orders through the legacy OMS."""
    adapter = FakeAdapter()
    risk_manager = RiskManager(
        limits=RiskLimits(),
        initial_capital=Decimal("100000"),
    )
    order_manager = OrderManager(exchange=adapter, risk_manager=risk_manager)
    algorithm = FakeAlgorithm(symbols=["BTCUSDT"], order_manager=order_manager)
    runtime = PollingTradingRuntime(
        mode="paper",
        exchange_name="binance",
        interval="1h",
        symbols=["BTCUSDT"],
        adapter=adapter,
        algorithm=algorithm,
        order_manager=order_manager,
        execute_orders=True,
    )

    summary = await runtime.run(iterations=1, poll_seconds=0, lookback=20)

    assert summary.dry_run is False
    assert summary.orders_executed == 1
    assert len(adapter.placed_orders) == 1
    assert adapter.connected is False


def test_param_coercion_falls_back_to_defaults_for_invalid_numeric_strings():
    """Malformed CLI params should not crash runtime strategy construction."""
    params = {
        "fast_period": "abc",
        "stop_loss_pct": "not-a-number",
    }

    assert _coerce_int_param(params, "fast_period", 20) == 20
    assert _coerce_float_param(params, "stop_loss_pct", 2.0) == 2.0
