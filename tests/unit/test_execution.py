"""Tests for order execution engine."""

import pytest
from datetime import datetime
from unittest.mock import Mock

from src.execution.engine import ExecutionEngine, ExecutionReport, OrderStatus
from src.broker import Order, OrderSide, OrderType


class TestExecutionReport:
    """Test cases for ExecutionReport dataclass."""

    def test_execution_report_creation(self):
        """Test creating an execution report."""
        report = ExecutionReport(
            order_id="order-123",
            symbol="AAPL",
            side="buy",
            quantity=100,
            filled_quantity=100,
            avg_price=150.0,
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            commission=1.0,
            realized_pnl=0.0,
        )

        assert report.order_id == "order-123"
        assert report.symbol == "AAPL"
        assert report.filled_quantity == 100
        assert report.status == OrderStatus.FILLED


class TestExecutionEngine:
    """Test cases for ExecutionEngine."""

    def test_initialization(self, mock_broker, mock_risk_manager):
        """Test engine initialization."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        assert engine.broker == mock_broker
        assert engine.risk_manager == mock_risk_manager
        assert engine.pending_orders == {}
        assert engine.filled_orders == {}

    def test_submit_order_success(self, mock_broker, mock_risk_manager, sample_order):
        """Test successful order submission."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        order_id = engine.submit_order(sample_order, reference_price=150.0)

        assert order_id == "test-order-123"
        assert "test-order-123" in engine.pending_orders
        mock_risk_manager.can_trade.assert_called_once()
        mock_risk_manager.validate_order.assert_called_once_with("AAPL", 100.0, 150.0)
        mock_broker.submit_order.assert_called_once_with(sample_order)

    def test_submit_order_rejects_when_trading_disabled(
        self,
        mock_broker,
        mock_risk_manager,
        sample_order,
    ):
        """Test fail-closed behavior when trading is disabled."""
        mock_risk_manager.can_trade.return_value = False
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        order_id = engine.submit_order(sample_order, reference_price=150.0)

        assert order_id is None
        mock_broker.submit_order.assert_not_called()
        mock_risk_manager.validate_order.assert_not_called()

    def test_submit_order_rejects_risk_validation_failure(
        self,
        mock_broker,
        mock_risk_manager,
        sample_order,
    ):
        """Test rejection when pre-trade validation fails."""
        mock_risk_manager.validate_order.return_value = (False, "Too much exposure")
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        order_id = engine.submit_order(sample_order, reference_price=150.0)

        assert order_id is None
        mock_broker.submit_order.assert_not_called()

    def test_submit_order_failure(self, mock_broker, mock_risk_manager, sample_order):
        """Test order submission failure."""
        mock_broker.submit_order.side_effect = Exception("API Error")
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        order_id = engine.submit_order(sample_order, reference_price=150.0)

        assert order_id is None
        assert len(engine.pending_orders) == 0

    def test_submit_order_requires_reference_price_for_market_orders(
        self,
        mock_broker,
        mock_risk_manager,
        sample_order,
    ):
        """Test market orders fail closed without a risk-validation price hint."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        order_id = engine.submit_order(sample_order)

        assert order_id is None
        mock_broker.submit_order.assert_not_called()

    def test_cancel_order_success(self, mock_broker, mock_risk_manager, sample_order):
        """Test successful order cancellation."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        # First submit an order
        engine.submit_order(sample_order, reference_price=150.0)

        # Then cancel it
        result = engine.cancel_order("test-order-123")

        assert result is True
        assert "test-order-123" not in engine.pending_orders
        mock_broker.cancel_order.assert_called_once_with("test-order-123")

    def test_cancel_order_not_pending(self, mock_broker, mock_risk_manager):
        """Test cancellation of non-pending order."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        result = engine.cancel_order("non-existent-order")

        assert result is False
        mock_broker.cancel_order.assert_not_called()

    def test_update_order_status_filled(self, mock_broker, mock_risk_manager):
        """Test updating status of filled order."""
        mock_broker.get_order.return_value = {
            "id": "test-order-123",
            "status": "filled",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "filled_qty": 100,
            "avg_price": 150.0,
        }

        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        # Submit and fill
        order = Order(symbol="AAPL", side=OrderSide.BUY, quantity=100, order_type=OrderType.MARKET)
        engine.submit_order(order, reference_price=150.0)

        report = engine.update_order_status("test-order-123")

        assert report is not None
        assert report.status == OrderStatus.FILLED
        assert report.filled_quantity == 100
        assert "test-order-123" in engine.filled_orders
        assert "test-order-123" not in engine.pending_orders

    def test_update_order_status_pending(self, mock_broker, mock_risk_manager):
        """Test updating status of still-pending order."""
        mock_broker.get_order.return_value = {
            "id": "test-order-123",
            "status": "submitted",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "filled_qty": 0,
            "avg_price": 0,
        }

        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)
        order = Order(symbol="AAPL", side=OrderSide.BUY, quantity=100, order_type=OrderType.MARKET)
        engine.submit_order(order, reference_price=150.0)

        report = engine.update_order_status("test-order-123")

        assert report is not None
        assert report.status == OrderStatus.SUBMITTED
        assert "test-order-123" in engine.pending_orders  # Still pending

    def test_update_order_status_not_found(self, mock_broker, mock_risk_manager):
        """Test updating status of unknown order."""
        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)

        report = engine.update_order_status("unknown-order")

        assert report is None

    def test_update_order_status_api_error(self, mock_broker, mock_risk_manager):
        """Test handling API error during status update."""
        mock_broker.get_order.side_effect = Exception("API Error")

        engine = ExecutionEngine(broker=mock_broker, risk_manager=mock_risk_manager)
        order = Order(symbol="AAPL", side=OrderSide.BUY, quantity=100, order_type=OrderType.MARKET)
        engine.submit_order(order, reference_price=150.0)

        report = engine.update_order_status("test-order-123")

        assert report is None


class TestOrderStatus:
    """Test cases for OrderStatus enum."""

    def test_order_status_values(self):
        """Test order status enum values."""
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.SUBMITTED.value == "submitted"
        assert OrderStatus.PARTIAL_FILL.value == "partial_fill"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"
