"""Order execution and management."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Protocol

from log_config import TradingLogger
from src.broker import OrderType


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class ExecutionReport:
    """Report of order execution."""

    order_id: str
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    avg_price: float
    status: OrderStatus
    timestamp: datetime
    commission: float = 0.0
    realized_pnl: float = 0.0


class RiskManagerProtocol(Protocol):
    """Minimal risk manager contract required by the execution boundary."""

    def can_trade(self) -> bool:
        """Return whether trading is currently allowed."""

    def validate_order(self, symbol: str, quantity: float, price: float) -> tuple[bool, str]:
        """Validate a proposed order before broker submission."""


class ExecutionEngine:
    """Handle order execution and fills."""

    def __init__(
        self,
        broker,
        risk_manager: RiskManagerProtocol,
        logger: TradingLogger | None = None,
    ):
        self.broker = broker
        self.risk_manager = risk_manager
        self.logger = logger or TradingLogger("ExecutionEngine")
        self.pending_orders: Dict[str, Any] = {}
        self.filled_orders: Dict[str, ExecutionReport] = {}

    @staticmethod
    def _resolve_reference_price(order, reference_price: Optional[float]) -> Optional[float]:
        if reference_price is not None:
            return float(reference_price)

        if getattr(order, "order_type", None) == OrderType.LIMIT and order.limit_price is not None:
            return float(order.limit_price)

        if getattr(order, "order_type", None) == OrderType.STOP and order.stop_price is not None:
            return float(order.stop_price)

        if getattr(order, "order_type", None) == OrderType.STOP_LIMIT:
            if order.limit_price is not None:
                return float(order.limit_price)
            if order.stop_price is not None:
                return float(order.stop_price)

        return None

    @staticmethod
    def _validate_order_shape(order, reference_price: Optional[float]) -> tuple[bool, str]:
        if not getattr(order, "symbol", ""):
            return False, "Order symbol is required"

        if getattr(order, "quantity", 0) <= 0:
            return False, "Order quantity must be positive"

        order_type = getattr(order, "order_type", None)
        if order_type == OrderType.LIMIT and order.limit_price is None:
            return False, "Limit orders require a limit price"

        if order_type == OrderType.STOP and order.stop_price is None:
            return False, "Stop orders require a stop price"

        if order_type == OrderType.STOP_LIMIT and (
            order.limit_price is None or order.stop_price is None
        ):
            return False, "Stop-limit orders require both stop and limit prices"

        if reference_price is None or reference_price <= 0:
            return False, "Order requires a positive reference price for risk validation"

        return True, "OK"

    def submit_order(self, order, *, reference_price: Optional[float] = None) -> Optional[str]:
        """Submit an order to the broker."""
        if self.risk_manager is None:
            self.logger.error("risk", "Execution requires a risk manager")
            return None

        resolved_price = self._resolve_reference_price(order, reference_price)
        valid, reason = self._validate_order_shape(order, resolved_price)
        if not valid:
            self.logger.error("validation", reason)
            return None

        if not self.risk_manager.can_trade():
            self.logger.risk_event("ORDER_REJECTED", "Trading not allowed by risk manager")
            return None

        allowed, reason = self.risk_manager.validate_order(
            order.symbol,
            float(order.quantity),
            float(resolved_price),
        )
        if not allowed:
            self.logger.risk_event("ORDER_REJECTED", reason)
            return None

        try:
            result = self.broker.submit_order(order)
            order_id = result.get("id")
            if order_id:
                self.pending_orders[order_id] = {
                    "order": order,
                    "submission": result,
                }
            return order_id
        except Exception as exc:
            self.logger.error("submit_order", f"Order submission failed: {exc}", exception=exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        if order_id not in self.pending_orders:
            return False

        success = self.broker.cancel_order(order_id)
        if success:
            del self.pending_orders[order_id]
        return success

    def update_order_status(self, order_id: str) -> Optional[ExecutionReport]:
        """Update status of a pending order."""
        if order_id not in self.pending_orders:
            return None

        try:
            pending = self.pending_orders[order_id]
            status = self.broker.get_order(order_id)
            if not isinstance(status, dict):
                status = pending.get("submission", {})

            status_value = status.get(
                "status", pending.get("submission", {}).get("status", "pending")
            )
            if not isinstance(status_value, str):
                status_value = "pending"

            report = ExecutionReport(
                order_id=order_id,
                symbol=status.get("symbol", ""),
                side=status.get("side", ""),
                quantity=status.get("qty", 0),
                filled_quantity=status.get("filled_qty", 0),
                avg_price=status.get("avg_price", 0),
                status=self._normalize_status(status_value),
                timestamp=datetime.now(),
            )

            if report.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}:
                self.filled_orders[order_id] = report
                self.pending_orders.pop(order_id, None)

            return report
        except Exception as exc:
            self.logger.error(
                "update_order_status",
                f"Failed to update order {order_id}: {exc}",
                exception=exc,
            )
            return None

    @staticmethod
    def _normalize_status(value: str) -> OrderStatus:
        """Normalize a status value into a valid OrderStatus."""
        try:
            return OrderStatus(value)
        except ValueError:
            return OrderStatus.PENDING
