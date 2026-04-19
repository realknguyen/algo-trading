"""Order Management System (OMS) for handling orders."""

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, Dict, Any, List, Callable, Set
from datetime import datetime

from adapters.base_adapter import (
    BaseExchangeAdapter,
    Order,
    OrderType,
    OrderSide,
    OrderStatus,
    TimeInForce,
)
from log_config import TradingLogger


class OMSStatus(Enum):
    """OMS operational status."""

    INITIALIZING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()


@dataclass
class OrderRequest:
    """Internal order request."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    # Reference price used for pre-trade risk validation, especially for market orders.
    reference_price: Optional[Decimal] = None
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC

    # Risk management
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None

    # Metadata
    algorithm_id: Optional[str] = None
    tags: Set[str] = field(default_factory=set)


@dataclass
class BracketOrder:
    """Bracket order containing entry, stop-loss, and take-profit."""

    entry_order: Order
    stop_loss_order: Optional[Order] = None
    take_profit_order: Optional[Order] = None

    parent_order_id: Optional[str] = None
    is_active: bool = True


class OrderManager:
    """Order Management System.

    Handles:
    - Order creation and validation
    - Order tracking and status updates
    - Bracket orders (entry + SL/TP)
    - Position sizing integration
    - Order event callbacks
    """

    def __init__(
        self,
        exchange: BaseExchangeAdapter,
        risk_manager,
        logger: Optional[TradingLogger] = None,
    ):
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.logger = logger or TradingLogger("OrderManager")

        # Order tracking
        self.orders: Dict[str, Order] = {}
        self.bracket_orders: Dict[str, BracketOrder] = {}
        self.pending_orders: Set[str] = set()
        self.filled_orders: Set[str] = set()
        self.cancelled_orders: Set[str] = set()

        # Event callbacks
        self.on_order_filled: Optional[Callable[[Order], None]] = None
        self.on_order_cancelled: Optional[Callable[[Order], None]] = None
        self.on_order_rejected: Optional[Callable[[Order, str], None]] = None

        # Status
        self.status = OMSStatus.INITIALIZING

        # Background task
        self._update_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the order manager."""
        self._running = True
        self.status = OMSStatus.RUNNING
        self._update_task = asyncio.create_task(self._order_update_loop())
        self.logger.logger.info("Order manager started")

    async def stop(self) -> None:
        """Stop the order manager."""
        self._running = False
        self.status = OMSStatus.STOPPED

        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        self.logger.logger.info("Order manager stopped")

    async def _order_update_loop(self) -> None:
        """Background loop to update order statuses."""
        while self._running:
            try:
                # Update all pending orders
                for order_id in list(self.pending_orders):
                    await self._update_order_status(order_id)

                await asyncio.sleep(1)  # Update every second
            except Exception as e:
                self.logger.error("update_loop", f"Error in update loop: {e}")
                await asyncio.sleep(5)

    async def _update_order_status(self, order_id: str) -> None:
        """Update status of a pending order."""
        order = self.orders.get(order_id)
        if not order:
            return

        try:
            updated_order = await self.exchange.get_order(order.symbol, order_id)

            # Check for status changes
            if updated_order.status != order.status:
                self.logger.order_status(order_id, updated_order.status.value)

                if updated_order.status == OrderStatus.FILLED:
                    self.pending_orders.discard(order_id)
                    self.filled_orders.add(order_id)
                    self.orders[order_id] = updated_order

                    if self.on_order_filled:
                        await self._safe_callback(self.on_order_filled, updated_order)

                elif updated_order.status == OrderStatus.CANCELLED:
                    self.pending_orders.discard(order_id)
                    self.cancelled_orders.add(order_id)
                    self.orders[order_id] = updated_order

                    if self.on_order_cancelled:
                        await self._safe_callback(self.on_order_cancelled, updated_order)

                elif updated_order.status == OrderStatus.REJECTED:
                    self.pending_orders.discard(order_id)
                    self.orders[order_id] = updated_order

                    if self.on_order_rejected:
                        await self._safe_callback(
                            self.on_order_rejected, updated_order, "Order rejected by exchange"
                        )

                else:
                    self.orders[order_id] = updated_order

        except Exception as e:
            self.logger.error("update_status", f"Failed to update order {order_id}: {e}")

    async def _safe_callback(self, callback, *args):
        """Safely execute a callback."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            self.logger.error("callback", f"Callback error: {e}")

    async def submit_order(
        self, request: OrderRequest, wait_for_fill: bool = False, timeout: float = 30.0
    ) -> Optional[Order]:
        """Submit a single order.

        Args:
            request: Order request details
            wait_for_fill: Whether to wait for order fill
            timeout: Timeout for waiting

        Returns:
            Order object if successful, None otherwise
        """
        # Validate order
        if not self._validate_order_request(request):
            if self.on_order_rejected:
                await self._safe_callback(
                    self.on_order_rejected,
                    self._build_order_from_request(request),
                    "Invalid order request",
                )
            return None

        allowed, reason = self._risk_allows_request(request)
        if not allowed:
            self.logger.risk_event("ORDER_REJECTED", reason or "Risk manager rejected order")
            if self.on_order_rejected:
                await self._safe_callback(
                    self.on_order_rejected,
                    self._build_order_from_request(request),
                    reason or "Risk manager rejected order",
                )
            return None

        # Create order
        order = self._build_order_from_request(request)

        try:
            # Submit to exchange
            filled_order = await self.exchange.place_order(order)

            # Track order
            if filled_order.order_id:
                self.orders[filled_order.order_id] = filled_order

                if filled_order.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]:
                    self.pending_orders.add(filled_order.order_id)
                elif filled_order.status == OrderStatus.FILLED:
                    self.filled_orders.add(filled_order.order_id)

                    if self.on_order_filled:
                        await self._safe_callback(self.on_order_filled, filled_order)

                self.logger.order_status(
                    filled_order.order_id,
                    filled_order.status.value,
                    float(filled_order.filled_quantity) if filled_order.filled_quantity else None,
                    float(filled_order.avg_fill_price) if filled_order.avg_fill_price else None,
                )

                # Wait for fill if requested
                if wait_for_fill and filled_order.status != OrderStatus.FILLED:
                    filled_order = await self._wait_for_fill(filled_order.order_id, timeout)

                return filled_order

        except Exception as e:
            self.logger.error("submit_order", f"Failed to submit order: {e}")
            if self.on_order_rejected:
                await self._safe_callback(self.on_order_rejected, order, str(e))

        return None

    async def submit_bracket_order(self, request: OrderRequest) -> Optional[BracketOrder]:
        """Submit a bracket order (entry + SL + TP).

        Args:
            request: Order request with stop_loss_pct and/or take_profit_pct

        Returns:
            BracketOrder if successful
        """
        # Submit entry order first
        entry_order = await self.submit_order(request)
        if not entry_order or not entry_order.order_id:
            return None

        bracket = BracketOrder(entry_order=entry_order)

        # Wait for entry fill before placing SL/TP
        # In a real system, you might want to do this asynchronously
        if entry_order.status == OrderStatus.FILLED:
            fill_price = entry_order.avg_fill_price or entry_order.price or Decimal("0")

            # Place stop-loss
            if request.stop_loss_pct and fill_price > 0:
                sl_side = OrderSide.SELL if request.side == OrderSide.BUY else OrderSide.BUY

                if request.side == OrderSide.BUY:
                    sl_price = fill_price * (1 - Decimal(str(request.stop_loss_pct)) / 100)
                else:
                    sl_price = fill_price * (1 + Decimal(str(request.stop_loss_pct)) / 100)

                sl_request = OrderRequest(
                    symbol=request.symbol,
                    side=sl_side,
                    order_type=OrderType.STOP_LOSS,
                    quantity=request.quantity,
                    reference_price=sl_price,
                    stop_price=sl_price,
                    time_in_force=TimeInForce.GTC,
                    algorithm_id=request.algorithm_id,
                    tags=set(request.tags) | {"protective_exit"},
                )

                sl_order = await self.submit_order(sl_request)
                bracket.stop_loss_order = sl_order

            # Place take-profit
            if request.take_profit_pct and fill_price > 0:
                tp_side = OrderSide.SELL if request.side == OrderSide.BUY else OrderSide.BUY

                if request.side == OrderSide.BUY:
                    tp_price = fill_price * (1 + Decimal(str(request.take_profit_pct)) / 100)
                else:
                    tp_price = fill_price * (1 - Decimal(str(request.take_profit_pct)) / 100)

                tp_request = OrderRequest(
                    symbol=request.symbol,
                    side=tp_side,
                    order_type=OrderType.LIMIT,
                    quantity=request.quantity,
                    reference_price=tp_price,
                    price=tp_price,
                    time_in_force=TimeInForce.GTC,
                    algorithm_id=request.algorithm_id,
                    tags=set(request.tags) | {"protective_exit"},
                )

                tp_order = await self.submit_order(tp_request)
                bracket.take_profit_order = tp_order

        self.bracket_orders[entry_order.order_id] = bracket
        return bracket

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        order = self.orders.get(order_id)
        if not order:
            self.logger.error("cancel_order", f"Order {order_id} not found")
            return False

        try:
            success = await self.exchange.cancel_order(order.symbol, order_id)
            if success:
                self.pending_orders.discard(order_id)
                self.logger.logger.info(f"Order {order_id} cancelled")
            return success
        except Exception as e:
            self.logger.error("cancel_order", f"Failed to cancel order: {e}")
            return False

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel all open orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            Number of orders cancelled
        """
        cancelled = 0
        for order_id in list(self.pending_orders):
            order = self.orders.get(order_id)
            if order and (symbol is None or order.symbol == symbol.upper()):
                if await self.cancel_order(order_id):
                    cancelled += 1
        return cancelled

    async def _wait_for_fill(self, order_id: str, timeout: float) -> Optional[Order]:
        """Wait for an order to be filled.

        Args:
            order_id: Order ID to wait for
            timeout: Maximum time to wait

        Returns:
            Filled order or None if timeout
        """
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            order = self.orders.get(order_id)
            if order and order.status == OrderStatus.FILLED:
                return order

            await asyncio.sleep(0.5)

        self.logger.logger.warning(f"Timeout waiting for fill of order {order_id}")
        return self.orders.get(order_id)

    def _validate_order_request(self, request: OrderRequest) -> bool:
        """Validate an order request.

        Args:
            request: Order request to validate

        Returns:
            True if valid
        """
        # Check quantity
        if request.quantity <= 0:
            self.logger.error("validation", "Order quantity must be positive")
            return False

        reference_price = self._resolve_reference_price(request)
        if reference_price is None or reference_price <= 0:
            self.logger.error(
                "validation", "Order requires a positive reference price for risk validation"
            )
            return False

        # Check price for limit orders
        if request.order_type in [OrderType.LIMIT, OrderType.STOP_LIMIT] and not request.price:
            self.logger.error("validation", "Limit orders require a price")
            return False

        # Check stop price for stop orders
        if (
            request.order_type in [OrderType.STOP_LOSS, OrderType.STOP_LIMIT]
            and not request.stop_price
        ):
            self.logger.error("validation", "Stop orders require a stop price")
            return False

        return True

    def _build_order_from_request(self, request: OrderRequest) -> Order:
        """Create an exchange order from an internal request."""
        return Order(
            symbol=request.symbol.upper(),
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            client_order_id=str(uuid.uuid4()),
        )

    def _resolve_reference_price(self, request: OrderRequest) -> Optional[Decimal]:
        """Resolve the effective price used for risk validation."""
        if request.reference_price is not None:
            return request.reference_price
        if request.price is not None:
            return request.price
        if request.stop_price is not None:
            return request.stop_price
        return None

    def _risk_allows_request(self, request: OrderRequest) -> tuple[bool, Optional[str]]:
        """Enforce the shared risk gate before any exchange submission."""
        if self.risk_manager is None:
            return False, "No risk manager configured"

        if "protective_exit" in request.tags:
            # Protective exits are intentionally permitted even when new risk-taking is blocked.
            return True, None

        if hasattr(self.risk_manager, "can_trade") and not self.risk_manager.can_trade():
            return False, "Trading disabled by risk manager"

        reference_price = self._resolve_reference_price(request)
        if reference_price is None:
            return False, "Missing reference price"

        if hasattr(self.risk_manager, "check_trade_risk"):
            return self.risk_manager.check_trade_risk(
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                price=reference_price,
                stop_loss_price=request.stop_price,
            )

        if hasattr(self.risk_manager, "validate_order"):
            return self.risk_manager.validate_order(
                request.symbol,
                float(request.quantity),
                float(reference_price),
            )

        return False, "Risk manager does not implement a supported validation interface"

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        orders = []
        for order_id in self.pending_orders:
            order = self.orders.get(order_id)
            if order and (symbol is None or order.symbol == symbol.upper()):
                orders.append(order)
        return orders

    def get_filled_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all filled orders."""
        orders = []
        for order_id in self.filled_orders:
            order = self.orders.get(order_id)
            if order and (symbol is None or order.symbol == symbol.upper()):
                orders.append(order)
        return orders

    def get_position_for_symbol(self, symbol: str) -> Decimal:
        """Calculate net position for a symbol from filled orders."""
        position = Decimal("0")

        for order_id in self.filled_orders:
            order = self.orders.get(order_id)
            if order and order.symbol == symbol.upper():
                if order.side == OrderSide.BUY:
                    position += order.filled_quantity
                else:
                    position -= order.filled_quantity

        return position
