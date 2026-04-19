"""Testnet/sandbox configuration and management for exchanges."""

import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class TestnetMode(str, Enum):
    """Testnet operation mode."""

    PAPER_TRADING = "paper"  # Simulated trading, no real money
    SANDBOX = "sandbox"  # Exchange testnet with fake money
    PRODUCTION = "production"  # Live trading (not testnet)


@dataclass
class TestnetConfig:
    """Configuration for testnet/sandbox trading.

    Attributes:
        exchange: Exchange name
        mode: Testnet mode (paper, sandbox, production)
        base_url: REST API base URL
        ws_url: WebSocket URL
        is_testnet: Whether this is a testnet configuration
        credentials_env_prefix: Environment variable prefix for credentials
        validate_orders: Whether to validate orders before submission
        max_order_value: Maximum order value allowed (for safety)
        allowed_symbols: List of allowed trading symbols (None = all)
        blocked_symbols: List of blocked trading symbols
    """

    exchange: str
    mode: TestnetMode
    base_url: str
    ws_url: Optional[str] = None
    is_testnet: bool = True
    credentials_env_prefix: str = ""
    validate_orders: bool = True
    max_order_value: Decimal = field(default_factory=lambda: Decimal("100000"))
    allowed_symbols: Optional[List[str]] = None
    blocked_symbols: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Set default credentials prefix if not provided."""
        if not self.credentials_env_prefix:
            self.credentials_env_prefix = f"{self.exchange.upper()}_TESTNET"


# ============================================================================
# Exchange-specific testnet configurations
# ============================================================================

BINANCE_TESTNET = TestnetConfig(
    exchange="binance",
    mode=TestnetMode.SANDBOX,
    base_url="https://testnet.binance.vision",
    ws_url="wss://testnet.binance.vision/ws",
    is_testnet=True,
    credentials_env_prefix="BINANCE_TESTNET",
    validate_orders=True,
    max_order_value=Decimal("10000"),
)

BINANCE_FUTURES_TESTNET = TestnetConfig(
    exchange="binance",
    mode=TestnetMode.SANDBOX,
    base_url="https://testnet.binancefuture.com",
    ws_url="wss://stream.binancefuture.com/ws",
    is_testnet=True,
    credentials_env_prefix="BINANCE_FUTURES_TESTNET",
    validate_orders=True,
    max_order_value=Decimal("50000"),
)

BINANCE_PRODUCTION = TestnetConfig(
    exchange="binance",
    mode=TestnetMode.PRODUCTION,
    base_url="https://api.binance.com",
    ws_url="wss://stream.binance.com:9443/ws",
    is_testnet=False,
    credentials_env_prefix="BINANCE",
    validate_orders=False,
    max_order_value=Decimal("1000000"),
)

COINBASE_SANDBOX = TestnetConfig(
    exchange="coinbase",
    mode=TestnetMode.SANDBOX,
    base_url="https://api-public.sandbox.pro.coinbase.com",
    ws_url="wss://ws-feed-public.sandbox.pro.coinbase.com",
    is_testnet=True,
    credentials_env_prefix="COINBASE_SANDBOX",
    validate_orders=True,
    max_order_value=Decimal("10000"),
)

COINBASE_PRODUCTION = TestnetConfig(
    exchange="coinbase",
    mode=TestnetMode.PRODUCTION,
    base_url="https://api.pro.coinbase.com",
    ws_url="wss://ws-feed.pro.coinbase.com",
    is_testnet=False,
    credentials_env_prefix="COINBASE",
    validate_orders=False,
    max_order_value=Decimal("1000000"),
)

KRAKEN_PRODUCTION = TestnetConfig(
    exchange="kraken",
    mode=TestnetMode.PAPER_TRADING,
    base_url="https://api.kraken.com",
    is_testnet=True,  # Kraken uses paper trading on main API
    credentials_env_prefix="KRAKEN",
    validate_orders=True,
    max_order_value=Decimal("10000"),
    blocked_symbols=["BTCUSD", "ETHUSD"],  # Block major pairs in test mode
)

# Registry of all testnet configurations
TESTNET_CONFIGS = {
    "binance_spot": BINANCE_TESTNET,
    "binance_futures": BINANCE_FUTURES_TESTNET,
    "binance": BINANCE_PRODUCTION,
    "coinbase_sandbox": COINBASE_SANDBOX,
    "coinbase": COINBASE_PRODUCTION,
    "kraken": KRAKEN_PRODUCTION,
}


def get_testnet_config(name: str) -> TestnetConfig:
    """Get a testnet configuration by name.

    Args:
        name: Configuration name (e.g., 'binance_spot', 'coinbase_sandbox')

    Returns:
        TestnetConfig instance

    Raises:
        ValueError: If configuration not found
    """
    if name not in TESTNET_CONFIGS:
        available = ", ".join(TESTNET_CONFIGS.keys())
        raise ValueError(f"Unknown testnet config: {name}. Available: {available}")

    return TESTNET_CONFIGS[name]


def list_testnet_configs() -> List[str]:
    """List all available testnet configuration names.

    Returns:
        List of configuration names
    """
    return list(TESTNET_CONFIGS.keys())


# ============================================================================
# Credentials management
# ============================================================================


@dataclass
class TestnetCredentials:
    """Credentials for testnet/sandbox trading.

    Attributes:
        api_key: API key
        api_secret: API secret
        passphrase: Optional passphrase (for Coinbase)
        additional: Additional exchange-specific credentials
    """

    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    additional: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str, passphrase_required: bool = False) -> "TestnetCredentials":
        """Load credentials from environment variables.

        Args:
            prefix: Environment variable prefix
            passphrase_required: Whether passphrase is required

        Returns:
            TestnetCredentials instance

        Raises:
            ValueError: If required credentials are missing
        """
        api_key = os.getenv(f"{prefix}_API_KEY")
        api_secret = os.getenv(f"{prefix}_API_SECRET")
        passphrase = os.getenv(f"{prefix}_PASSPHRASE")

        if not api_key:
            raise ValueError(f"Missing environment variable: {prefix}_API_KEY")
        if not api_secret:
            raise ValueError(f"Missing environment variable: {prefix}_API_SECRET")
        if passphrase_required and not passphrase:
            raise ValueError(f"Missing environment variable: {prefix}_PASSPHRASE")

        return cls(api_key=api_key, api_secret=api_secret, passphrase=passphrase)

    def to_dict(self, mask: bool = True) -> Dict[str, str]:
        """Convert to dictionary.

        Args:
            mask: Whether to mask sensitive values

        Returns:
            Dictionary representation
        """

        def mask_value(value: str) -> str:
            if not value or len(value) <= 8:
                return "***"
            return value[:4] + "..." + value[-4:]

        result = {
            "api_key": mask_value(self.api_key) if mask else self.api_key,
            "api_secret": mask_value(self.api_secret) if mask else self.api_secret,
        }

        if self.passphrase:
            result["passphrase"] = mask_value(self.passphrase) if mask else self.passphrase

        if self.additional:
            result["additional"] = {
                k: mask_value(v) if mask else v for k, v in self.additional.items()
            }

        return result


class TestnetCredentialManager:
    """Manager for testnet credentials."""

    def __init__(self):
        """Initialize credential manager."""
        self._credentials: Dict[str, TestnetCredentials] = {}

    def load_from_env(self, config: TestnetConfig) -> TestnetCredentials:
        """Load credentials for a config from environment.

        Args:
            config: Testnet configuration

        Returns:
            Loaded credentials
        """
        passphrase_required = config.exchange == "coinbase"

        credentials = TestnetCredentials.from_env(
            config.credentials_env_prefix, passphrase_required=passphrase_required
        )

        self._credentials[config.exchange] = credentials

        logger.info(
            f"Loaded credentials for {config.exchange} "
            f"(prefix: {config.credentials_env_prefix})"
        )

        return credentials

    def load_all_from_env(self) -> Dict[str, TestnetCredentials]:
        """Load credentials for all testnet configs from environment.

        Returns:
            Dictionary of exchange -> credentials
        """
        for name, config in TESTNET_CONFIGS.items():
            try:
                if config.is_testnet:
                    self.load_from_env(config)
            except ValueError as e:
                logger.warning(f"Skipping {name}: {e}")

        return self._credentials

    def get(self, exchange: str) -> Optional[TestnetCredentials]:
        """Get credentials for an exchange.

        Args:
            exchange: Exchange name

        Returns:
            Credentials or None if not loaded
        """
        return self._credentials.get(exchange)

    def set(self, exchange: str, credentials: TestnetCredentials) -> None:
        """Set credentials for an exchange.

        Args:
            exchange: Exchange name
            credentials: Credentials to set
        """
        self._credentials[exchange] = credentials

    def clear(self, exchange: Optional[str] = None) -> None:
        """Clear credentials.

        Args:
            exchange: Specific exchange, or None to clear all
        """
        if exchange:
            self._credentials.pop(exchange, None)
        else:
            self._credentials.clear()


# ============================================================================
# Testnet order validation
# ============================================================================


class TestnetValidationError(Exception):
    """Exception raised when order validation fails in testnet mode."""

    pass


class TestnetOrderValidator:
    """Validator for orders in testnet/sandbox mode.

    Ensures no real money is at risk by validating:
    - Order size limits
    - Symbol restrictions
    - Price reasonableness
    - Testnet mode confirmation
    """

    def __init__(self, config: TestnetConfig):
        """Initialize validator.

        Args:
            config: Testnet configuration
        """
        self.config = config

    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        order_type: str = "market",
    ) -> Dict[str, Any]:
        """Validate an order for testnet trading.

        Args:
            symbol: Trading symbol
            side: Order side (buy/sell)
            quantity: Order quantity
            price: Order price (for limit orders)
            order_type: Order type

        Returns:
            Validation result dictionary

        Raises:
            TestnetValidationError: If validation fails
        """
        errors = []
        warnings = []

        # Check if testnet mode is enabled
        if not self.config.is_testnet:
            errors.append("Not in testnet mode - real money may be at risk!")

        # Normalize symbol
        symbol = symbol.upper().replace("-", "").replace("/", "")

        # Check allowed symbols
        if self.config.allowed_symbols and symbol not in self.config.allowed_symbols:
            errors.append(f"Symbol {symbol} not in allowed list")

        # Check blocked symbols
        if symbol in [s.upper() for s in self.config.blocked_symbols]:
            errors.append(f"Symbol {symbol} is blocked for testnet trading")

        # Validate quantity
        if quantity <= 0:
            errors.append(f"Invalid quantity: {quantity}")

        # Validate price for limit orders
        if price is not None:
            if price <= 0:
                errors.append(f"Invalid price: {price}")

            # Check order value
            order_value = quantity * price
            if order_value > self.config.max_order_value:
                errors.append(
                    f"Order value {order_value} exceeds maximum " f"{self.config.max_order_value}"
                )
            elif order_value > self.config.max_order_value * Decimal("0.8"):
                warnings.append(
                    f"Order value {order_value} is close to maximum "
                    f"{self.config.max_order_value}"
                )

        # Additional testnet warnings
        if self.config.mode == TestnetMode.SANDBOX:
            warnings.append("Using sandbox - trades are simulated")
        elif self.config.mode == TestnetMode.PAPER_TRADING:
            warnings.append("Using paper trading mode")

        if errors:
            raise TestnetValidationError(f"Order validation failed: {'; '.join(errors)}")

        result = {
            "valid": True,
            "symbol": symbol,
            "side": side.lower(),
            "quantity": str(quantity),
            "price": str(price) if price else None,
            "order_type": order_type,
            "mode": self.config.mode.value,
            "is_testnet": self.config.is_testnet,
        }

        if warnings:
            result["warnings"] = warnings
            logger.warning(f"Order validation warnings: {warnings}")

        logger.info(f"Order validated for {symbol}: {result}")

        return result

    def validate_market_order(
        self, symbol: str, side: str, quantity: Decimal, current_price: Optional[Decimal] = None
    ) -> Dict[str, Any]:
        """Validate a market order.

        Args:
            symbol: Trading symbol
            side: Order side
            quantity: Order quantity
            current_price: Current market price (for value check)

        Returns:
            Validation result
        """
        estimated_value = None
        if current_price:
            estimated_value = quantity * current_price

        result = self.validate_order(symbol, side, quantity, estimated_value, "market")

        if current_price:
            result["estimated_value"] = str(estimated_value)
            result["estimated_price"] = str(current_price)

        return result

    def validate_cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Validate a cancel order request.

        Args:
            order_id: Order ID to cancel
            symbol: Trading symbol

        Returns:
            Validation result
        """
        if not self.config.is_testnet:
            raise TestnetValidationError("Not in testnet mode - real orders may be cancelled!")

        return {
            "valid": True,
            "action": "cancel",
            "order_id": order_id,
            "symbol": symbol.upper(),
            "is_testnet": self.config.is_testnet,
        }


# ============================================================================
# Testnet adapter wrapper
# ============================================================================


class TestnetAdapter:
    """Wrapper for exchange adapters that enforces testnet safety.

    This wrapper adds an additional layer of protection when using
    testnet/sandbox environments to prevent accidental live trading.
    """

    def __init__(
        self, adapter: Any, config: TestnetConfig, credentials: Optional[TestnetCredentials] = None
    ):
        """Initialize testnet adapter wrapper.

        Args:
            adapter: Exchange adapter instance
            config: Testnet configuration
            credentials: Optional credentials
        """
        self.adapter = adapter
        self.config = config
        self.credentials = credentials
        self.validator = TestnetOrderValidator(config)

        # Ensure we're in testnet mode
        if not config.is_testnet:
            logger.warning(f"WARNING: {config.exchange} adapter is not in testnet mode!")

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        order_type: str = "market",
        **kwargs,
    ) -> Any:
        """Place an order with testnet validation.

        Args:
            symbol: Trading symbol
            side: Order side
            quantity: Order quantity
            price: Order price
            order_type: Order type
            **kwargs: Additional order parameters

        Returns:
            Order result
        """
        # Validate order
        validation = self.validator.validate_order(symbol, side, quantity, price, order_type)

        logger.info(f"Placing testnet order: {validation}")

        # Call underlying adapter
        return await self.adapter.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            **kwargs,
        )

    async def cancel_order(self, order_id: str, symbol: str) -> Any:
        """Cancel an order with testnet validation.

        Args:
            order_id: Order ID
            symbol: Trading symbol

        Returns:
            Cancel result
        """
        validation = self.validator.validate_cancel_order(order_id, symbol)

        logger.info(f"Cancelling testnet order: {validation}")

        return await self.adapter.cancel_order(order_id, symbol)

    def __getattr__(self, name: str) -> Any:
        """Delegate other attributes to underlying adapter."""
        return getattr(self.adapter, name)
