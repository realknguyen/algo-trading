"""Order execution and management."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


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


class ExecutionEngine:
    """Handle order execution and fills."""

    def __init__(self, broker):
        self.broker = broker
        self.pending_orders: Dict[str, Any] = {}
        self.filled_orders: Dict[str, ExecutionReport] = {}

    def submit_order(self, order) -> Optional[str]:
        """Submit an order to the broker."""
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
            print(f"Order submission failed: {exc}")
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

            status_value = status.get("status", pending.get("submission", {}).get("status", "pending"))
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
            print(f"Failed to update order {order_id}: {exc}")
            return None

    @staticmethod
    def _normalize_status(value: str) -> OrderStatus:
        """Normalize a status value into a valid OrderStatus."""
        try:
            return OrderStatus(value)
        except ValueError:
            return OrderStatus.PENDING
