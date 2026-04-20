"""Security-focused tests for the legacy order manager path."""

from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from adapters.base_adapter import Order, OrderSide, OrderStatus, OrderType
from order_management.order_manager import OrderManager, OrderRequest


def _filled_order(symbol: str, side: OrderSide, quantity: Decimal, price: Decimal) -> Order:
    """Build a filled adapter order for async exchange tests."""
    return Order(
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=price,
        order_id=f"{symbol}-{side.value}",
        status=OrderStatus.FILLED,
        filled_quantity=quantity,
        avg_fill_price=price,
    )


@pytest.mark.asyncio
async def test_submit_order_rejects_when_trading_disabled():
    """Legacy OMS should fail closed when the risk manager blocks trading."""
    exchange = Mock()
    exchange.place_order = AsyncMock()

    risk_manager = Mock()
    risk_manager.can_trade.return_value = False

    manager = OrderManager(exchange=exchange, risk_manager=risk_manager)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        reference_price=Decimal("50000"),
    )

    result = await manager.submit_order(request)

    assert result is None
    exchange.place_order.assert_not_called()
    risk_manager.check_trade_risk.assert_not_called()


@pytest.mark.asyncio
async def test_bracket_order_child_legs_use_intentional_protective_policy():
    """Protective exit legs should still be submitted after a valid filled entry."""
    exchange = Mock()
    exchange.place_order = AsyncMock(
        side_effect=[
            _filled_order("BTCUSDT", OrderSide.BUY, Decimal("0.1"), Decimal("50000")),
            _filled_order("BTCUSDT", OrderSide.SELL, Decimal("0.1"), Decimal("49000")),
            _filled_order("BTCUSDT", OrderSide.SELL, Decimal("0.1"), Decimal("53000")),
        ]
    )

    risk_manager = Mock()
    risk_manager.can_trade.return_value = True
    risk_manager.check_trade_risk.return_value = (True, None)

    manager = OrderManager(exchange=exchange, risk_manager=risk_manager)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        reference_price=Decimal("50000"),
        stop_loss_pct=2.0,
        take_profit_pct=6.0,
    )

    bracket = await manager.submit_bracket_order(request)

    assert bracket is not None
    assert bracket.stop_loss_order is not None
    assert bracket.take_profit_order is not None
    assert exchange.place_order.await_count == 3
    # Only the entry order should invoke trade-risk validation.
    risk_manager.check_trade_risk.assert_called_once()
