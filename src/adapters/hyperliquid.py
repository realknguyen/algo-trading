"""Hyperliquid exchange adapter implementation.

This module provides a comprehensive adapter for the Hyperliquid decentralized
exchange, supporting perpetual futures trading with EIP-712 typed data signing.

Features:
    - EIP-712 typed data signing for Ethereum wallet authentication
    - Perpetual futures trading (no spot markets on Hyperliquid)
    - Cross-margin and isolated margin support
    - Real-time WebSocket feeds with auto-reconnect
    - Funding rate tracking and predictions
    - Liquidation price calculations
    - Vault API support
    - Arbitrum L2 integration

Reference: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""

import asyncio
import json
import logging
import secrets
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Union, AsyncIterator
from dataclasses import dataclass, field
from collections import defaultdict

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

# EIP-712 and Ethereum imports
try:
    import msgpack
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak, to_hex
    from cryptography.hazmat.primitives import hashes, serialization

    ETH_AVAILABLE = True
except ImportError:
    ETH_AVAILABLE = False

# Import from parent adapters module
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from adapters.base_adapter import (
        BaseExchangeAdapter,
        Order,
        OrderType,
        OrderSide,
        OrderStatus,
        TimeInForce,
        Ticker,
        Position,
        Balance,
        Candle,
        OrderBook,
        AccountInfo,
        RetryConfig,
    )
    from adapters.exceptions import (
        ExchangeError,
        ExchangeConnectionError,
        AuthenticationError,
        RateLimitError,
        OrderError,
        InsufficientFundsError,
        InvalidSymbolError,
        WebSocketError,
        DataValidationError,
    )
except ImportError:
    from base_adapter import (
        BaseExchangeAdapter,
        Order,
        OrderType,
        OrderSide,
        OrderStatus,
        TimeInForce,
        Ticker,
        Position,
        Balance,
        Candle,
        OrderBook,
        AccountInfo,
        RetryConfig,
    )
    from exceptions import (
        ExchangeError,
        ExchangeConnectionError,
        AuthenticationError,
        RateLimitError,
        OrderError,
        InsufficientFundsError,
        InvalidSymbolError,
        WebSocketError,
        DataValidationError,
    )


logger = logging.getLogger(__name__)


# Hyperliquid-specific types
Tif = str  # "Gtc", "Ioc", or "Alo"


class HyperliquidOrderType:
    """Order type constants for Hyperliquid."""

    LIMIT = "limit"
    MARKET = "market"
    TRIGGER = "trigger"


class HyperliquidTIF:
    """Time in force constants."""

    GTC = "Gtc"  # Good Till Cancelled
    IOC = "Ioc"  # Immediate or Cancel
    ALO = "Alo"  # Add Liquidity Only (Post Only)


class HyperliquidError(ExchangeError):
    """Raised when Hyperliquid API returns an error."""

    def __init__(self, message: str, code: int = None, status: str = None):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class FundingRate:
    """Funding rate dataclass.

    Attributes:
        coin: Trading pair symbol
        funding_rate: Current funding rate
        predicted_funding: Predicted next funding rate
        premium: Current premium
        time: Timestamp
    """

    coin: str
    funding_rate: Decimal
    predicted_funding: Optional[Decimal] = None
    premium: Optional[Decimal] = None
    time: Optional[int] = None


@dataclass
class AssetContext:
    """Asset context/market metadata.

    Attributes:
        coin: Trading pair symbol
        mark_px: Mark price
        oracle_px: Oracle/index price
        mid_px: Mid price
        open_interest: Open interest
        funding: Current funding rate
        day_ntl_vlm: 24h volume in notional USD
        prev_day_px: Previous day price
        impact_pxs: Impact bid/ask prices
    """

    coin: str
    mark_px: Decimal
    oracle_px: Decimal
    mid_px: Optional[Decimal] = None
    open_interest: Decimal = field(default_factory=lambda: Decimal("0"))
    funding: Decimal = field(default_factory=lambda: Decimal("0"))
    day_ntl_vlm: Decimal = field(default_factory=lambda: Decimal("0"))
    prev_day_px: Optional[Decimal] = None
    impact_pxs: Optional[List[Decimal]] = None


class HyperliquidAdapter(BaseExchangeAdapter):
    """Hyperliquid exchange adapter with EIP-712 signing and WebSocket support.

    Supports perpetual futures trading on Hyperliquid's Arbitrum L2 deployment.
    Uses EIP-712 typed data signing for all authenticated operations.

    Args:
        api_key: Ethereum wallet address (public key)
        api_secret: Ethereum private key (with or without 0x prefix)
        sandbox: Use testnet (default: True)
        vault_address: Optional vault address for delegated trading
        rate_limit_per_second: Requests per second limit (default: 10.0)
        default_slippage: Default slippage for market orders (default: 0.05 = 5%)

    Example:
        >>> adapter = HyperliquidAdapter(
        ...     api_key="0x1234...",  # Wallet address
        ...     api_secret="0xabcd...",  # Private key
        ...     sandbox=True
        ... )
        >>> async with adapter:
        ...     # Get account info
        ...     account = await adapter.get_account()
        ...
        ...     # Place a limit order
        ...     order = Order(
        ...         symbol="BTC",
        ...         side=OrderSide.BUY,
        ...         order_type=OrderType.LIMIT,
        ...         quantity=Decimal("0.1"),
        ...         price=Decimal("40000")
        ...     )
        ...     placed = await adapter.place_order(order)
    """

    # Exchange identification
    exchange_name = "hyperliquid"

    # API URLs
    MAINNET_API_URL = "https://api.hyperliquid.xyz"
    TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
    MAINNET_WS_URL = "wss://api.hyperliquid.xyz/ws"
    TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"

    # EIP-712 Domain
    EIP712_DOMAIN = {
        "name": "Exchange",
        "version": "1",
        "chainId": 1337,
        "verifyingContract": "0x0000000000000000000000000000000000000000",
    }

    # Agent EIP-712 Types
    AGENT_TYPES = {
        "Agent": [
            {"name": "source", "type": "string"},
            {"name": "connectionId", "type": "bytes32"},
        ],
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
    }

    # User-signed action types
    USD_SEND_TYPES = [
        {"name": "hyperliquidChain", "type": "string"},
        {"name": "destination", "type": "string"},
        {"name": "amount", "type": "string"},
        {"name": "time", "type": "uint64"},
    ]

    WITHDRAW_TYPES = [
        {"name": "hyperliquidChain", "type": "string"},
        {"name": "destination", "type": "string"},
        {"name": "amount", "type": "string"},
        {"name": "time", "type": "uint64"},
    ]

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        vault_address: Optional[str] = None,
        rate_limit_per_second: float = 10.0,
        default_slippage: float = 0.05,
        **kwargs,
    ):
        if not ETH_AVAILABLE:
            raise ImportError(
                "Hyperliquid adapter requires eth-account, msgpack, and cryptography. "
                "Install with: pip install eth-account msgpack cryptography"
            )

        # Validate and store wallet
        self.wallet_address = (
            api_key.lower() if api_key.startswith("0x") else f"0x{api_key.lower()}"
        )
        self.private_key = api_secret if api_secret.startswith("0x") else f"0x{api_secret}"
        self.vault_address = vault_address.lower() if vault_address else None
        self.default_slippage = default_slippage
        self.expires_after: Optional[int] = None

        # Initialize Ethereum account
        try:
            self.wallet = Account.from_key(self.private_key)
            if self.wallet.address.lower() != self.wallet_address.lower():
                logger.warning(
                    f"Wallet address mismatch: provided {self.wallet_address}, "
                    f"but private key corresponds to {self.wallet.address}"
                )
        except Exception as e:
            raise AuthenticationError(f"Invalid private key: {e}", exchange=self.exchange_name)

        # Metadata caches
        self._coin_to_asset: Dict[str, int] = {}
        self._asset_to_coin: Dict[int, str] = {}
        self._asset_to_sz_decimals: Dict[int, int] = {}
        self._meta_loaded = False

        # WebSocket state
        self._ws_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._ws_callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._ws_reconnect_attempts: Dict[str, int] = {}
        self._ws_running: Dict[str, bool] = {}
        self._ws_tasks: Dict[str, asyncio.Task] = {}
        self._ws_subscriptions: Dict[str, List[Dict]] = defaultdict(list)

        # Select appropriate URLs
        base_url = self.TESTNET_API_URL if sandbox else self.MAINNET_API_URL
        ws_url = self.TESTNET_WS_URL if sandbox else self.MAINNET_WS_URL

        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            rate_limit_per_second=rate_limit_per_second,
            sandbox=sandbox,
            ws_url=ws_url,
            auth_type="eip712",  # Custom auth type for EIP-712 signing
            **kwargs,
        )

    # ============ Metadata Management ============

    async def _load_meta(self) -> None:
        """Load exchange metadata including asset mappings."""
        if self._meta_loaded:
            return

        # Load perpetual metadata
        meta = await self._post_info({"type": "meta"})
        for asset, asset_info in enumerate(meta.get("universe", [])):
            coin = asset_info["name"]
            self._coin_to_asset[coin] = asset
            self._asset_to_coin[asset] = coin
            self._asset_to_sz_decimals[asset] = asset_info.get("szDecimals", 8)

        # Load spot metadata (assets start at 10000)
        spot_meta = await self._post_info({"type": "spotMeta"})
        for spot_info in spot_meta.get("universe", []):
            asset = spot_info["index"] + 10000
            coin = spot_info["name"]
            self._coin_to_asset[coin] = asset
            self._asset_to_coin[asset] = coin
            # Get decimals from token info
            tokens = spot_meta.get("tokens", [])
            if spot_info["tokens"] and len(spot_info["tokens"]) > 0:
                base_idx = spot_info["tokens"][0]
                if base_idx < len(tokens):
                    self._asset_to_sz_decimals[asset] = tokens[base_idx].get("szDecimals", 8)

        self._meta_loaded = True

    def _name_to_asset(self, name: str) -> int:
        """Convert coin name to asset ID."""
        if name not in self._coin_to_asset:
            raise InvalidSymbolError(
                f"Unknown symbol: {name}", exchange=self.exchange_name, symbol=name
            )
        return self._coin_to_asset[name]

    def _asset_to_name(self, asset: int) -> str:
        """Convert asset ID to coin name."""
        return self._asset_to_coin.get(asset, str(asset))

    def _get_sz_decimals(self, asset: int) -> int:
        """Get size decimals for an asset."""
        return self._asset_to_sz_decimals.get(asset, 8)

    # ============ Utility Methods ============

    @staticmethod
    def _float_to_wire(x: float) -> str:
        """Convert float to wire format with precision handling."""
        rounded = f"{x:.8f}"
        if abs(float(rounded) - x) >= 1e-12:
            raise ValueError(f"float_to_wire causes rounding: {x}")
        if rounded == "-0":
            rounded = "0"
        normalized = Decimal(rounded).normalize()
        return f"{normalized:f}"

    @staticmethod
    def _float_to_int(x: float, power: int) -> int:
        """Convert float to integer with specified decimal places."""
        with_decimals = x * 10**power
        if abs(round(with_decimals) - with_decimals) >= 1e-3:
            raise ValueError(f"float_to_int causes rounding: {x}")
        return round(with_decimals)

    @staticmethod
    def _get_timestamp_ms() -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _address_to_bytes(self, address: str) -> bytes:
        """Convert Ethereum address to bytes."""
        addr = address[2:] if address.startswith("0x") else address
        return bytes.fromhex(addr)

    # ============ EIP-712 Signing ============

    def _action_hash(
        self,
        action: Dict,
        vault_address: Optional[str],
        nonce: int,
        expires_after: Optional[int] = None,
    ) -> bytes:
        """Compute action hash for EIP-712 signing."""
        data = msgpack.packb(action)
        data += nonce.to_bytes(8, "big")

        if vault_address is None:
            data += b"\x00"
        else:
            data += b"\x01"
            data += self._address_to_bytes(vault_address)

        if expires_after is not None:
            data += b"\x00"
            data += expires_after.to_bytes(8, "big")

        return keccak(data)

    def _construct_phantom_agent(self, hash_bytes: bytes, is_mainnet: bool) -> Dict:
        """Construct phantom agent for L1 signing."""
        return {"source": "a" if is_mainnet else "b", "connectionId": hash_bytes}

    def _l1_payload(self, phantom_agent: Dict) -> Dict:
        """Build EIP-712 payload for L1 action."""
        return {
            "domain": self.EIP712_DOMAIN,
            "types": self.AGENT_TYPES,
            "primaryType": "Agent",
            "message": phantom_agent,
        }

    def _user_signed_payload(
        self, primary_type: str, payload_types: List[Dict], action: Dict, chain_id: int = 0x66EEE
    ) -> Dict:
        """Build EIP-712 payload for user-signed action."""
        return {
            "domain": {
                "name": "HyperliquidSignTransaction",
                "version": "1",
                "chainId": chain_id,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "types": {
                primary_type: payload_types,
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
            },
            "primaryType": primary_type,
            "message": action,
        }

    def _sign_inner(self, data: Dict) -> Dict[str, Union[str, int]]:
        """Sign EIP-712 data and return signature components."""
        structured_data = encode_typed_data(full_message=data)
        signed = self.wallet.sign_message(structured_data)
        return {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}

    def _sign_l1_action(self, action: Dict, vault_address: Optional[str], nonce: int) -> Dict:
        """Sign an L1 action with EIP-712."""
        hash_bytes = self._action_hash(action, vault_address, nonce, self.expires_after)
        phantom_agent = self._construct_phantom_agent(hash_bytes, not self._sandbox)
        data = self._l1_payload(phantom_agent)
        return self._sign_inner(data)

    def _sign_user_signed_action(
        self, action: Dict, payload_types: List[Dict], primary_type: str
    ) -> Dict:
        """Sign a user-signed action (transfers, withdrawals)."""
        action["signatureChainId"] = "0x66eee"
        action["hyperliquidChain"] = "Mainnet" if not self._sandbox else "Testnet"
        data = self._user_signed_payload(primary_type, payload_types, action)
        return self._sign_inner(data)

    # ============ HTTP Request Helpers ============

    async def _post_info(self, payload: Dict) -> Any:
        """Make a request to the /info endpoint (no auth required)."""
        async with self._rate_limiter:
            response = await self._client.post("/info", json=payload)
            response.raise_for_status()
            return response.json()

    async def _post_exchange(self, action: Dict, signature: Dict, nonce: int) -> Any:
        """Make an authenticated request to the /exchange endpoint."""
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
            "vaultAddress": (
                self.vault_address
                if action.get("type") not in ["usdClassTransfer", "sendAsset"]
                else None
            ),
            "expiresAfter": self.expires_after,
        }

        async with self._rate_limiter:
            response = await self._client.post("/exchange", json=payload)
            response.raise_for_status()
            result = response.json()

            # Check for errors in response
            if isinstance(result, dict) and result.get("status") == "err":
                error_msg = result.get("response", "Unknown error")
                raise HyperliquidError(error_msg, status="err")

            return result

    # ============ Connection Management ============

    async def connect(self) -> bool:
        """Connect to Hyperliquid and validate credentials.

        Returns:
            True if connection successful

        Raises:
            ExchangeConnectionError: If connection fails
            AuthenticationError: If credentials are invalid
        """
        try:
            # Load metadata
            await self._load_meta()

            # Verify wallet by checking user state
            user_state = await self._post_info(
                {"type": "clearinghouseState", "user": self.wallet_address}
            )

            if user_state is None:
                raise AuthenticationError(
                    "Failed to verify wallet address", exchange=self.exchange_name
                )

            self._connected = True
            logger.info(f"Connected to Hyperliquid {'testnet' if self._sandbox else 'mainnet'}")
            return True

        except httpx.HTTPError as e:
            raise ExchangeConnectionError(f"Failed to connect: {e}", exchange=self.exchange_name)

    # ============ Account Methods ============

    async def get_account(self) -> AccountInfo:
        """Get account information.

        Returns:
            AccountInfo with account details
        """
        await self._load_meta()

        user_state = await self._post_info(
            {"type": "clearinghouseState", "user": self.wallet_address}
        )

        # Get user role for permissions
        try:
            user_role = await self._post_info({"type": "userRole", "user": self.wallet_address})
            permissions = [user_role.get("role", "user")] if user_role else ["user"]
        except Exception:
            permissions = ["user"]

        return AccountInfo(
            account_id=self.wallet_address, account_type="perpetual", permissions=permissions
        )

    async def get_balances(self) -> List[Balance]:
        """Get account balances.

        Returns:
            List of Balance objects. Hyperliquid uses USDC as collateral.
        """
        await self._load_meta()

        user_state = await self._post_info(
            {"type": "clearinghouseState", "user": self.wallet_address}
        )

        balances = []

        # Cross margin summary shows total account value
        margin_summary = user_state.get("crossMarginSummary", {})
        account_value = Decimal(str(margin_summary.get("accountValue", "0")))
        total_margin_used = Decimal(str(margin_summary.get("totalMarginUsed", "0")))

        if account_value > 0:
            balances.append(
                Balance(
                    asset="USDC",
                    free=account_value - total_margin_used,
                    locked=total_margin_used,
                    total=account_value,
                )
            )

        # Get withdrawable amount
        withdrawable = Decimal(str(user_state.get("withdrawable", "0")))

        return balances

    # ============ Trading Methods ============

    def _order_type_to_wire(self, order_type: OrderType) -> Dict:
        """Convert internal order type to Hyperliquid wire format."""
        if order_type == OrderType.MARKET:
            return {"limit": {"tif": "Ioc"}}
        elif order_type == OrderType.LIMIT:
            return {"limit": {"tif": "Gtc"}}
        elif order_type == OrderType.STOP_LOSS:
            return {"trigger": {"triggerPx": "0", "isMarket": True, "tpsl": "sl"}}
        elif order_type == OrderType.TAKE_PROFIT:
            return {"trigger": {"triggerPx": "0", "isMarket": True, "tpsl": "tp"}}
        else:
            return {"limit": {"tif": "Gtc"}}

    def _tif_to_wire(self, tif: TimeInForce) -> str:
        """Convert internal TIF to Hyperliquid format."""
        mapping = {
            TimeInForce.GTC: "Gtc",
            TimeInForce.IOC: "Ioc",
            TimeInForce.FOK: "Ioc",  # Hyperliquid doesn't have FOK, use IOC
        }
        return mapping.get(tif, "Gtc")

    def _build_order_wire(self, order: Order, asset: int) -> Dict:
        """Build order wire format for API."""
        is_buy = order.side == OrderSide.BUY

        # Get price - use limit price or calculate for market orders
        if order.price:
            price = float(order.price)
        else:
            # For market orders, we'll need to get current price
            price = 0.0

        # Get size decimals
        sz_decimals = self._get_sz_decimals(asset)

        order_wire = {
            "a": asset,
            "b": is_buy,
            "p": self._float_to_wire(price),
            "s": self._float_to_wire(float(order.quantity)),
            "r": False,  # reduce_only - can be set based on order params
            "t": self._order_type_to_wire(order.order_type),
        }

        # Add TIF for limit orders
        if order.order_type == OrderType.LIMIT:
            order_wire["t"]["limit"]["tif"] = self._tif_to_wire(order.time_in_force)

        # Add client order ID if provided
        if order.client_order_id:
            order_wire["c"] = order.client_order_id

        return order_wire

    async def place_order(self, order: Order) -> Order:
        """Place a new order on Hyperliquid.

        Args:
            order: Order to place

        Returns:
            Updated order with exchange order ID

        Raises:
            OrderError: If order placement fails
        """
        await self._load_meta()

        asset = self._name_to_asset(order.symbol)

        # For market orders, get aggressive price
        if order.order_type == OrderType.MARKET:
            mids = await self._post_info({"type": "allMids"})
            mid_price = float(mids.get(order.symbol, 0))
            slippage_mult = (
                (1 + self.default_slippage)
                if order.side == OrderSide.BUY
                else (1 - self.default_slippage)
            )
            order.price = Decimal(str(mid_price * slippage_mult))
            # Round to appropriate decimals
            sz_decimals = self._get_sz_decimals(asset)
            price_decimals = 6 if asset < 10000 else 8  # perp vs spot
            order.price = Decimal(str(round(float(order.price), price_decimals - sz_decimals)))

        order_wire = self._build_order_wire(order, asset)

        timestamp = self._get_timestamp_ms()
        action = {"type": "order", "orders": [order_wire], "grouping": "na"}

        signature = self._sign_l1_action(action, self.vault_address, timestamp)
        result = await self._post_exchange(action, signature, timestamp)

        # Parse response
        if result.get("status") == "ok":
            response_data = result.get("response", {}).get("data", {})
            statuses = response_data.get("statuses", [])

            if statuses and len(statuses) > 0:
                status = statuses[0]
                if "resting" in status:
                    order.order_id = str(status["resting"]["oid"])
                    order.status = OrderStatus.OPEN
                elif "filled" in status:
                    order.order_id = str(status["filled"]["oid"])
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = order.quantity
                else:
                    order.status = OrderStatus.REJECTED

        order.created_at = time.time()
        return order

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancel an existing order.

        Args:
            order_id: Exchange order ID (oid)
            symbol: Trading symbol (required for Hyperliquid)

        Returns:
            True if cancellation was successful

        Raises:
            OrderError: If cancellation fails
        """
        if not symbol:
            raise OrderError(
                "Symbol is required for Hyperliquid cancel_order", exchange=self.exchange_name
            )

        await self._load_meta()
        asset = self._name_to_asset(symbol)

        timestamp = self._get_timestamp_ms()
        action = {"type": "cancel", "cancels": [{"a": asset, "o": int(order_id)}]}

        signature = self._sign_l1_action(action, self.vault_address, timestamp)
        result = await self._post_exchange(action, signature, timestamp)

        if result.get("status") == "ok":
            response_data = result.get("response", {}).get("data", {})
            statuses = response_data.get("statuses", [])
            if statuses:
                return "success" in statuses[0]

        return False

    async def get_order_status(self, order_id: str, symbol: Optional[str] = None) -> Order:
        """Get order status by ID.

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol

        Returns:
            Order with current status
        """
        result = await self._post_info(
            {"type": "orderStatus", "user": self.wallet_address, "oid": int(order_id)}
        )

        # Parse order status response
        order_data = result.get("order", {})

        order = Order(
            symbol=symbol or order_data.get("coin", "UNKNOWN"),
            side=OrderSide.BUY if order_data.get("side") == "B" else OrderSide.SELL,
            order_type=OrderType.LIMIT if "limitPx" in order_data else OrderType.MARKET,
            quantity=Decimal(str(order_data.get("origSz", "0"))),
            price=Decimal(str(order_data.get("limitPx", "0"))),
            order_id=str(order_id),
        )

        # Determine status
        status = order_data.get("status")
        if status == "open":
            order.status = OrderStatus.OPEN
        elif status == "filled":
            order.status = OrderStatus.FILLED
        elif status == "canceled":
            order.status = OrderStatus.CANCELLED
        elif status == "partiallyFilled":
            order.status = OrderStatus.PARTIALLY_FILLED
        else:
            order.status = OrderStatus.PENDING

        order.filled_quantity = Decimal(str(order_data.get("filledSz", "0")))

        return order

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders.

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of open orders
        """
        result = await self._post_info({"type": "openOrders", "user": self.wallet_address})

        orders = []
        for order_data in result:
            order_symbol = order_data.get("coin", "")

            if symbol and order_symbol != symbol:
                continue

            order = Order(
                symbol=order_symbol,
                side=OrderSide.BUY if order_data.get("side") == "B" else OrderSide.SELL,
                order_type=OrderType.LIMIT,  # Open orders are always limit
                quantity=Decimal(str(order_data.get("sz", "0"))),
                price=Decimal(str(order_data.get("limitPx", "0"))),
                order_id=str(order_data.get("oid", "")),
                status=OrderStatus.OPEN,
            )
            orders.append(order)

        return orders

    # ============ Position Methods ============

    async def get_positions(self) -> List[Position]:
        """Get current perpetual positions.

        Returns:
            List of open positions
        """
        await self._load_meta()

        user_state = await self._post_info(
            {"type": "clearinghouseState", "user": self.wallet_address}
        )

        positions = []
        asset_positions = user_state.get("assetPositions", [])

        for pos_data in asset_positions:
            position = pos_data.get("position", {})

            symbol = position.get("coin", "")
            szi = Decimal(str(position.get("szi", "0")))

            if szi == 0:
                continue

            # Get leverage info
            leverage_data = position.get("leverage", {})
            leverage = Decimal(str(leverage_data.get("value", "1")))
            margin_mode = leverage_data.get("type", "cross")

            pos = Position(
                symbol=symbol,
                quantity=szi,  # Positive for long, negative for short
                avg_entry_price=Decimal(str(position.get("entryPx", "0"))),
                current_price=Decimal(str(position.get("markPx", "0"))),
                unrealized_pnl=Decimal(str(position.get("unrealizedPnl", "0"))),
                realized_pnl=Decimal(str(position.get("realizedPnl", "0"))),
                leverage=leverage,
                margin_mode=margin_mode,
            )
            positions.append(pos)

        return positions

    async def get_funding_rate(
        self, symbol: Optional[str] = None
    ) -> Union[FundingRate, List[FundingRate]]:
        """Get funding rate information.

        Args:
            symbol: Trading symbol (if None, returns all funding rates)

        Returns:
            FundingRate or list of FundingRate objects
        """
        # Get meta and asset contexts for funding data
        result = await self._post_info({"type": "metaAndAssetCtxs"})

        if not result or len(result) < 2:
            return [] if symbol is None else None

        meta = result[0]
        contexts = result[1]

        funding_rates = []

        for asset_info, ctx in zip(meta.get("universe", []), contexts):
            coin = asset_info.get("name", "")

            if symbol and coin != symbol:
                continue

            funding_rate = FundingRate(
                coin=coin,
                funding_rate=Decimal(str(ctx.get("funding", "0"))),
                premium=Decimal(str(ctx.get("premium", "0"))) if "premium" in ctx else None,
                time=self._get_timestamp_ms(),
            )

            funding_rates.append(funding_rate)

            if symbol and coin == symbol:
                return funding_rate

        return funding_rates if not symbol else None

    # ============ Market Data Methods ============

    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current ticker for symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC", "ETH")

        Returns:
            Ticker data with prices and volume
        """
        await self._load_meta()

        # Get all mids (mark prices)
        mids = await self._post_info({"type": "allMids"})
        mark_px = Decimal(str(mids.get(symbol, "0")))

        # Get detailed context
        result = await self._post_info({"type": "metaAndAssetCtxs"})

        if result and len(result) >= 2:
            meta = result[0]
            contexts = result[1]

            for i, asset_info in enumerate(meta.get("universe", [])):
                if asset_info.get("name") == symbol and i < len(contexts):
                    ctx = contexts[i]

                    # Get orderbook for bid/ask
                    orderbook = await self.get_orderbook(symbol, limit=1)

                    return Ticker(
                        symbol=symbol,
                        bid=orderbook.best_bid or mark_px,
                        ask=orderbook.best_ask or mark_px,
                        last=mark_px,
                        volume=Decimal(str(ctx.get("dayNtlVlm", "0"))),
                        timestamp=time.time(),
                        high_24h=Decimal(str(ctx.get("prevDayPx", "0")))
                        * Decimal("1.1"),  # Approximate
                        low_24h=Decimal(str(ctx.get("prevDayPx", "0")))
                        * Decimal("0.9"),  # Approximate
                    )

        # Fallback if detailed data not available
        return Ticker(
            symbol=symbol,
            bid=mark_px,
            ask=mark_px,
            last=mark_px,
            volume=Decimal("0"),
            timestamp=time.time(),
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """Get L2 orderbook for symbol.

        Args:
            symbol: Trading pair symbol
            limit: Number of levels (not directly used in Hyperliquid API)

        Returns:
            OrderBook with bids and asks
        """
        await self._load_meta()
        coin = self._name_to_asset(symbol)

        result = await self._post_info({"type": "l2Book", "coin": symbol})

        levels = result.get("levels", [[], []])

        bids = []
        asks = []

        # Parse bids (level 0)
        for level in levels[0]:
            bids.append([Decimal(str(level.get("px", "0"))), Decimal(str(level.get("sz", "0")))])

        # Parse asks (level 1)
        for level in levels[1]:
            asks.append([Decimal(str(level.get("px", "0"))), Decimal(str(level.get("sz", "0")))])

        return OrderBook(
            symbol=symbol, bids=bids, asks=asks, timestamp=time.time(), sequence=result.get("time")
        )

    async def get_historical_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[Candle]:
        """Get historical OHLCV candles.

        Args:
            symbol: Trading pair symbol
            interval: Candle interval (e.g., "1m", "5m", "1h", "1d")
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            limit: Maximum candles to retrieve (max 1000)

        Returns:
            List of Candle objects
        """
        await self._load_meta()

        # Default time range if not specified
        if end_time is None:
            end_time = self._get_timestamp_ms()
        if start_time is None:
            # Default to 500 candles back
            interval_ms = self._interval_to_ms(interval)
            start_time = end_time - (interval_ms * min(limit, 500))

        result = await self._post_info(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_time,
                    "endTime": end_time,
                },
            }
        )

        candles = []
        for candle_data in result:
            candle = Candle(
                timestamp=candle_data.get("t", 0),
                open=Decimal(str(candle_data.get("o", "0"))),
                high=Decimal(str(candle_data.get("h", "0"))),
                low=Decimal(str(candle_data.get("l", "0"))),
                close=Decimal(str(candle_data.get("c", "0"))),
                volume=Decimal(str(candle_data.get("v", "0"))),
            )
            candles.append(candle)

        return candles

    def _interval_to_ms(self, interval: str) -> int:
        """Convert interval string to milliseconds."""
        units = {
            "m": 60 * 1000,
            "h": 60 * 60 * 1000,
            "d": 24 * 60 * 60 * 1000,
        }

        unit = interval[-1]
        value = int(interval[:-1])

        return value * units.get(unit, 60 * 1000)

    # ============ WebSocket Methods ============

    async def subscribe_market_data(
        self, symbols: List[str], channels: List[str], callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Subscribe to real-time market data via WebSocket.

        Args:
            symbols: List of trading symbols
            channels: Data channels ("ticker", "trades", "orderbook", "candles")
            callback: Function to call with data updates
        """
        await self._load_meta()

        # Map channel names to Hyperliquid subscription types
        channel_mapping = {
            "ticker": "activeAssetCtx",
            "trades": "trades",
            "orderbook": "l2Book",
            "candles": "candle",
            "bbo": "bbo",
            "allMids": "allMids",
        }

        for symbol in symbols:
            for channel in channels:
                hl_channel = channel_mapping.get(channel, channel)

                subscription = {"type": hl_channel, "coin": symbol}
                if channel == "candles":
                    subscription["interval"] = "1m"  # Default interval

                await self._subscribe_ws(subscription, callback)

    async def subscribe_to_trades(self, symbol: str, callback: Callable[[Dict], None]) -> None:
        """Subscribe to real-time trade updates.

        Args:
            symbol: Trading symbol
            callback: Function to receive trade data
        """
        await self._load_meta()
        await self._subscribe_ws({"type": "trades", "coin": symbol}, callback)

    async def subscribe_to_orderbook(self, symbol: str, callback: Callable[[Dict], None]) -> None:
        """Subscribe to orderbook updates.

        Args:
            symbol: Trading symbol
            callback: Function to receive orderbook data
        """
        await self._load_meta()
        await self._subscribe_ws({"type": "l2Book", "coin": symbol}, callback)

    async def subscribe_to_ticker(self, symbol: str, callback: Callable[[Dict], None]) -> None:
        """Subscribe to mark price/ticker updates.

        Args:
            symbol: Trading symbol
            callback: Function to receive ticker data
        """
        await self._load_meta()
        await self._subscribe_ws({"type": "activeAssetCtx", "coin": symbol}, callback)

    async def subscribe_to_candles(
        self, symbol: str, interval: str, callback: Callable[[Dict], None]
    ) -> None:
        """Subscribe to candlestick updates.

        Args:
            symbol: Trading symbol
            interval: Candle interval (e.g., "1m", "5m", "1h")
            callback: Function to receive candle data
        """
        await self._load_meta()
        await self._subscribe_ws({"type": "candle", "coin": symbol, "interval": interval}, callback)

    async def subscribe_to_user(self, callback: Callable[[Dict], None]) -> None:
        """Subscribe to private user updates (fills, orders, etc.).

        Args:
            callback: Function to receive user event data
        """
        await self._subscribe_ws({"type": "userEvents", "user": self.wallet_address}, callback)

    async def _subscribe_ws(self, subscription: Dict, callback: Callable[[Dict], None]) -> None:
        """Internal WebSocket subscription with auto-reconnect."""
        sub_key = json.dumps(subscription, sort_keys=True)

        # Store subscription for reconnection
        self._ws_subscriptions[sub_key] = {"subscription": subscription, "callback": callback}

        # Start WebSocket connection if not running
        if "main" not in self._ws_running or not self._ws_running["main"]:
            self._ws_running["main"] = True
            self._ws_reconnect_attempts["main"] = 0
            self._ws_tasks["main"] = asyncio.create_task(
                self._ws_loop(sub_key, subscription, callback)
            )
        else:
            # Subscribe to existing connection
            ws = self._ws_connections.get("main")
            if ws and ws.open:
                await ws.send(json.dumps({"method": "subscribe", "subscription": subscription}))

    async def _ws_loop(
        self, sub_key: str, subscription: Dict, callback: Callable[[Dict], None]
    ) -> None:
        """WebSocket connection loop with exponential backoff reconnect."""
        reconnect_delay = 1.0
        max_reconnect_delay = 60.0

        while self._ws_running.get("main", False):
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._ws_connections["main"] = ws
                    self._ws_reconnect_attempts["main"] = 0
                    reconnect_delay = 1.0

                    # Subscribe to all stored subscriptions
                    for sub_data in self._ws_subscriptions.values():
                        await ws.send(
                            json.dumps(
                                {"method": "subscribe", "subscription": sub_data["subscription"]}
                            )
                        )

                    # Handle messages
                    async for message in ws:
                        try:
                            data = json.loads(message)

                            # Handle pong
                            if data.get("channel") == "pong":
                                continue

                            # Find matching callback
                            for sub_data in self._ws_subscriptions.values():
                                if self._matches_subscription(data, sub_data["subscription"]):
                                    try:
                                        sub_data["callback"](data)
                                    except Exception as e:
                                        logger.error(f"Error in WebSocket callback: {e}")
                                    break

                        except json.JSONDecodeError:
                            logger.warning(f"Received non-JSON WebSocket message: {message}")
                        except Exception as e:
                            logger.error(f"Error processing WebSocket message: {e}")

            except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            # Reconnect with exponential backoff
            if self._ws_running.get("main", False):
                attempts = self._ws_reconnect_attempts.get("main", 0)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                self._ws_reconnect_attempts["main"] = attempts + 1

                logger.info(
                    f"Reconnecting WebSocket in {reconnect_delay}s (attempt {attempts + 1})"
                )
                await asyncio.sleep(reconnect_delay)

    def _matches_subscription(self, data: Dict, subscription: Dict) -> bool:
        """Check if a WebSocket message matches a subscription."""
        channel = data.get("channel", "")
        sub_type = subscription.get("type", "")

        # Map channels to types
        channel_to_type = {
            "trades": "trades",
            "l2Book": "l2Book",
            "activeAssetCtx": "activeAssetCtx",
            "candle": "candle",
            "user": "userEvents",
            "userFills": "userFills",
            "orderUpdates": "orderUpdates",
            "allMids": "allMids",
            "bbo": "bbo",
        }

        if channel_to_type.get(channel) != sub_type:
            return False

        # Check coin match for symbol-specific subscriptions
        if "coin" in subscription:
            data_coin = data.get("data", {}).get("coin", "")
            if data_coin and data_coin != subscription["coin"]:
                return False

        # Check user match for user-specific subscriptions
        if "user" in subscription:
            data_user = data.get("data", {}).get("user", "")
            if data_user and data_user != subscription["user"]:
                return False

        return True

    async def unsubscribe_market_data(
        self, symbols: Optional[List[str]] = None, channels: Optional[List[str]] = None
    ) -> None:
        """Unsubscribe from market data.

        Args:
            symbols: Symbols to unsubscribe (None = all)
            channels: Channels to unsubscribe (None = all)
        """
        ws = self._ws_connections.get("main")
        if not ws or not ws.open:
            return

        # Find matching subscriptions and remove them
        to_remove = []
        for key, sub_data in list(self._ws_subscriptions.items()):
            sub = sub_data["subscription"]

            match_symbol = symbols is None or sub.get("coin") in symbols
            match_channel = channels is None or sub.get("type") in channels

            if match_symbol and match_channel:
                to_remove.append(key)
                await ws.send(json.dumps({"method": "unsubscribe", "subscription": sub}))

        for key in to_remove:
            del self._ws_subscriptions[key]

    async def disconnect(self) -> None:
        """Disconnect from exchange and cleanup resources."""
        # Stop WebSocket
        self._ws_running["main"] = False

        if "main" in self._ws_tasks:
            self._ws_tasks["main"].cancel()
            try:
                await self._ws_tasks["main"]
            except asyncio.CancelledError:
                pass

        ws = self._ws_connections.get("main")
        if ws:
            await ws.close()

        # Close HTTP client
        await self._client.aclose()
        self._connected = False

        logger.info("Disconnected from Hyperliquid")

    # ============ Hyperliquid-Specific Methods ============

    async def update_leverage(self, symbol: str, leverage: int, is_cross: bool = True) -> bool:
        """Update leverage for a symbol.

        Args:
            symbol: Trading symbol
            leverage: Leverage value (1-50)
            is_cross: True for cross-margin, False for isolated

        Returns:
            True if successful
        """
        await self._load_meta()
        asset = self._name_to_asset(symbol)

        timestamp = self._get_timestamp_ms()
        action = {
            "type": "updateLeverage",
            "asset": asset,
            "isCross": is_cross,
            "leverage": leverage,
        }

        signature = self._sign_l1_action(action, self.vault_address, timestamp)
        result = await self._post_exchange(action, signature, timestamp)

        return result.get("status") == "ok"

    async def get_liquidation_price(self, symbol: str) -> Optional[Decimal]:
        """Get liquidation price for a position.

        Args:
            symbol: Trading symbol

        Returns:
            Liquidation price or None if no position
        """
        user_state = await self._post_info(
            {"type": "clearinghouseState", "user": self.wallet_address}
        )

        for pos_data in user_state.get("assetPositions", []):
            position = pos_data.get("position", {})
            if position.get("coin") == symbol:
                liq_px = position.get("liquidationPx")
                if liq_px is not None:
                    return Decimal(str(liq_px))

        return None

    async def get_user_fees(self) -> Dict:
        """Get user fee schedule and volume.

        Returns:
            Dictionary with fee information
        """
        return await self._post_info({"type": "userFees", "user": self.wallet_address})

    async def get_funding_history(
        self, symbol: str, start_time: int, end_time: Optional[int] = None
    ) -> List[Dict]:
        """Get funding rate history for a symbol.

        Args:
            symbol: Trading symbol
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds

        Returns:
            List of funding history entries
        """
        await self._load_meta()

        payload = {"type": "fundingHistory", "coin": symbol, "startTime": start_time}

        if end_time:
            payload["endTime"] = end_time

        return await self._post_info(payload)

    async def approve_agent(self, name: Optional[str] = None) -> tuple:
        """Create and approve an agent wallet for delegated trading.

        This allows creating a sub-key that can trade on behalf of the
        main wallet with limited permissions.

        Args:
            name: Optional name for the agent

        Returns:
            Tuple of (result_dict, agent_private_key)
        """
        agent_key = "0x" + secrets.token_hex(32)
        agent_account = Account.from_key(agent_key)

        timestamp = self._get_timestamp_ms()
        action = {
            "type": "approveAgent",
            "agentAddress": agent_account.address,
            "agentName": name or "",
            "nonce": timestamp,
        }

        if name is None:
            del action["agentName"]

        signature = self._sign_user_signed_action(
            action,
            [
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "agentAddress", "type": "address"},
                {"name": "agentName", "type": "string"},
                {"name": "nonce", "type": "uint64"},
            ],
            "HyperliquidTransaction:ApproveAgent",
        )

        result = await self._post_exchange(action, signature, timestamp)
        return result, agent_key

    async def usd_transfer(self, destination: str, amount: float) -> Dict:
        """Transfer USDC to another address.

        Args:
            destination: Destination wallet address
            amount: Amount to transfer

        Returns:
            Transfer result
        """
        timestamp = self._get_timestamp_ms()
        action = {
            "type": "usdSend",
            "destination": destination,
            "amount": str(amount),
            "time": timestamp,
        }

        signature = self._sign_user_signed_action(
            action, self.USD_SEND_TYPES, "HyperliquidTransaction:UsdSend"
        )

        return await self._post_exchange(action, signature, timestamp)

    async def withdraw(self, destination: str, amount: float) -> Dict:
        """Withdraw USDC to Arbitrum.

        Args:
            destination: Destination address on Arbitrum
            amount: Amount to withdraw

        Returns:
            Withdrawal result
        """
        timestamp = self._get_timestamp_ms()
        action = {
            "type": "withdraw3",
            "destination": destination,
            "amount": str(amount),
            "time": timestamp,
        }

        signature = self._sign_user_signed_action(
            action, self.WITHDRAW_TYPES, "HyperliquidTransaction:Withdraw"
        )

        return await self._post_exchange(action, signature, timestamp)


# Register the adapter
try:
    from adapters import register_adapter

    register_adapter("hyperliquid")(HyperliquidAdapter)
except ImportError:
    pass
