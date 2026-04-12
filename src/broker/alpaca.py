"""Alpaca REST broker adapter."""

from __future__ import annotations

import requests

from . import BaseBroker, Order, OrderSide, OrderType, Position


class AlpacaBroker(BaseBroker):
    """Minimal Alpaca broker adapter used by local execution tests."""

    BASE_URL = "https://api.alpaca.markets"
    PAPER_URL = "https://paper-api.alpaca.markets"

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        super().__init__(api_key=api_key, api_secret=api_secret, paper=paper)
        self.base_url = self.PAPER_URL if paper else self.BASE_URL
        self.session = None

    def _get_session(self):
        if self.session is None:
            self.connect()
        return self.session

    def connect(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            }
        )
        self.session.get(f"{self.base_url}/v2/account").raise_for_status()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        if self.session is not None:
            self.session.close()
            self.session = None

    def get_account(self) -> dict:
        response = self._get_session().get(f"{self.base_url}/v2/account")
        response.raise_for_status()
        data = response.json()
        return {
            "id": data["id"],
            "cash": float(data["cash"]),
            "buying_power": float(data["buying_power"]),
            "portfolio_value": float(data["portfolio_value"]),
        }

    def get_positions(self) -> list[Position]:
        response = self._get_session().get(f"{self.base_url}/v2/positions")
        response.raise_for_status()
        rows = response.json()
        positions: list[Position] = []
        for row in rows:
            positions.append(
                Position(
                    symbol=row["symbol"],
                    quantity=float(row["qty"]),
                    avg_entry_price=float(row["avg_entry_price"]),
                    current_price=float(row["current_price"]),
                    unrealized_pl=float(row["unrealized_pl"]),
                )
            )
        return positions

    def _build_order_payload(self, order: Order) -> dict:
        payload = {
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "side": order.side.value,
            "type": order.order_type.value,
            "time_in_force": order.time_in_force,
        }
        if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
        return payload

    def submit_order(self, order: Order) -> dict:
        response = self._get_session().post(
            f"{self.base_url}/v2/orders",
            json=self._build_order_payload(order),
        )
        response.raise_for_status()
        data = response.json()

        return {
            "id": str(data["id"]),
            "status": data.get("status", ""),
            "symbol": data.get("symbol", order.symbol),
            "side": data.get("side", order.side.value),
            "qty": float(data.get("qty", order.quantity)),
            "filled_qty": float(data.get("filled_qty", 0)),
            "avg_price": float(data.get("filled_avg_price", 0.0)),
            "created_at": data.get("created_at"),
        }

    def cancel_order(self, order_id: str) -> bool:
        response = self._get_session().delete(f"{self.base_url}/v2/orders/{order_id}")
        response.raise_for_status()
        return True

    def get_order(self, order_id: str) -> dict:
        response = self._get_session().get(f"{self.base_url}/v2/orders/{order_id}")
        response.raise_for_status()
        data = response.json()
        return {
            "id": str(data["id"]),
            "status": data.get("status", ""),
            "symbol": data.get("symbol"),
            "side": data.get("side"),
            "qty": float(data.get("qty", 0)),
            "filled_qty": float(data.get("filled_qty", 0)),
            "avg_price": float(data.get("filled_avg_price", 0.0)),
        }
