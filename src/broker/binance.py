"""Binance REST broker adapter."""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from . import BaseBroker, Order, OrderType, Position


class BinanceBroker(BaseBroker):
    """Minimal Binance broker adapter used by local execution tests."""

    BASE_URL = "https://api.binance.com"
    TESTNET_URL = "https://testnet.binance.vision"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, paper: bool = True):
        super().__init__(api_key=api_key, api_secret=api_secret, paper=paper)
        self.testnet = testnet
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.session = None

    def _get_session(self):
        if self.session is None:
            self.connect()
        return self.session

    def _sign(self, params: dict) -> str:
        payload = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def connect(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self.session.get(f"{self.base_url}/api/v3/account", timeout=10).raise_for_status()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        if self.session is not None:
            self.session.close()
            self.session = None

    def get_account(self) -> dict:
        payload = self._signed_request("GET", "/api/v3/account")
        balances = payload.get("balances", [])
        cash = 0.0
        currency = "USDT"
        for balance in balances:
            if balance.get("asset") == "USDT":
                cash = float(balance.get("free", 0.0))
                break
        return {"id": str(payload.get("accountId")), "cash": cash, "currency": currency}

    def _signed_request(self, method: str, path: str, params: dict | None = None):
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        full_url = f"{self.base_url}{path}"

        if method.upper() == "GET":
            response = self._get_session().get(full_url, params=params)
        elif method.upper() == "POST":
            response = self._get_session().post(full_url, data=params)
        elif method.upper() == "DELETE":
            response = self._get_session().delete(full_url, params=params)
        else:
            response = self._get_session().request(method=method, url=full_url, params=params)

        response.raise_for_status()
        return response.json()

    def _build_order_params(self, order) -> dict:
        params = {
            "symbol": order.symbol,
            "side": order.side.value.upper(),
            "type": order.order_type.value.upper(),
            "quantity": str(order.quantity),
            "timeInForce": order.time_in_force.upper(),
        }
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            params["price"] = str(order.limit_price)
        return params

    def submit_order(self, order) -> dict:
        data = self._signed_request("POST", "/api/v3/order", self._build_order_params(order))
        status = str(data.get("status", "")).lower()
        return {
            "id": str(data.get("orderId")),
            "status": status,
            "symbol": data.get("symbol", order.symbol),
            "side": data.get("side", order.side.value).lower(),
            "qty": float(data.get("origQty", 0)),
            "filled_qty": float(data.get("executedQty", 0)),
            "avg_price": float(data.get("avgPrice", 0.0)),
        }

    def cancel_order(self, order_id: str) -> bool:
        # Symbol is required for spot endpoint; defer to symbol-specific cancel in tests.
        if not self._connected:
            raise RuntimeError("Broker is not connected")
        response = self._get_session().delete(f"{self.base_url}/api/v3/order")
        response.raise_for_status()
        return True

    def cancel_order_with_symbol(self, order_id: str, symbol: str) -> bool:
        self._get_session().delete(
            f"{self.base_url}/api/v3/order",
            params={"orderId": order_id, "symbol": symbol, "timestamp": int(time.time() * 1000)},
        ).raise_for_status()
        return True

    def get_order(self, order_id: str, symbol: str | None = None) -> dict:
        if symbol is None:
            raise ValueError("symbol is required for Binance get_order")
        data = self._signed_request(
            "GET",
            "/api/v3/order",
            {"orderId": order_id, "symbol": symbol},
        )
        status = str(data.get("status", "")).lower()
        return {
            "id": str(data.get("orderId")),
            "status": status,
            "symbol": data.get("symbol"),
            "side": str(data.get("side", "")).lower(),
            "qty": float(data.get("origQty", 0)),
            "filled_qty": float(data.get("executedQty", 0)),
            "avg_price": float(data.get("avgPrice", 0.0)),
        }

    def get_positions(self) -> list[Position]:
        account = self._signed_request("GET", "/api/v3/account")
        balances = account.get("balances", [])
        positions: list[Position] = []

        quote_assets = {"USDT", "BUSD", "FDUSD", "TUSD", "USDC", "DAI", "USDP"}
        ticker_price = None

        for balance in balances:
            asset = balance.get("asset")
            free = float(balance.get("free", 0.0))
            locked = float(balance.get("locked", 0.0))
            total = free + locked
            if not asset or asset in quote_assets or total <= 0:
                continue

            if ticker_price is None:
                ticker = self._get_session().get(
                    f"{self.base_url}/api/v3/ticker/price",
                    params={"symbol": f"{asset}USDT"},
                )
                ticker.raise_for_status()
                ticker_json = ticker.json()
                ticker_price = float(ticker_json.get("price", 0.0))

            current_price = float(ticker_price or 0.0)
            positions.append(
                Position(
                    symbol=asset,
                    quantity=total,
                    avg_entry_price=0.0,
                    current_price=current_price,
                    unrealized_pl=0.0,
                )
            )

        return positions
