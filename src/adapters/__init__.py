"""Exchange adapters for algo-trading system.

This package provides a unified interface for interacting with multiple
cryptocurrency exchanges. It includes:

- BaseExchangeAdapter: Abstract base class for all exchange adapters
- ExchangeFactory: Factory pattern for creating exchange adapters
- Authentication modules: HMAC, RSA, and Ed25519 signing support
- Comprehensive exception hierarchy for error handling

Example:
    >>> from src.adapters import ExchangeFactory, Order, OrderSide, OrderType
    >>>
    >>> # Create adapter using factory
    >>> adapter = ExchangeFactory.create(
    ...     exchange="binance",
    ...     api_key="your_api_key",
    ...     api_secret="your_api_secret",
    ...     sandbox=True
    ... )
    >>>
    >>> # Use adapter with context manager
    >>> async with adapter:
    ...     account = await adapter.get_account()
    ...     ticker = await adapter.get_ticker("BTCUSD")
    ...
    >>> # Or use manual connect/disconnect
    >>> await adapter.connect()
    >>> balances = await adapter.get_balances()
    >>> await adapter.disconnect()

Custom Adapter Registration:
    >>> from src.adapters import register_adapter, BaseExchangeAdapter
    >>>
    >>> @register_adapter("myexchange")
    >>> class MyExchangeAdapter(BaseExchangeAdapter):
    ...     exchange_name = "myexchange"
    ...     # ... implementation
"""

import os
from typing import Type, Dict, Any, Optional, TypeVar
from functools import wraps

# Core exports
from src.adapters.base_adapter import (
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

# Exception exports
from src.adapters.exceptions import (
    ExchangeError,
    ExchangeConnectionError,
    AuthenticationError,
    RateLimitError,
    OrderError,
    InsufficientFundsError,
    InvalidSymbolError,
    MarketClosedError,
    WebSocketError,
    DataValidationError,
)

# Auth exports
from src.adapters.auth import (
    AuthConfig,
    RequestSigner,
    HMACSigner,
    BinanceHMACSigner,
    CoinbaseHMACSigner,
    RSASigner,
    Ed25519Signer,
    ClockSkewManager,
    create_signer,
)

# Type variable for adapter classes
T = TypeVar("T", bound=BaseExchangeAdapter)

# Registry of exchange adapters
_adapter_registry: Dict[str, Type[BaseExchangeAdapter]] = {}


def register_adapter(name: str):
    """Decorator to register an exchange adapter class.

    This decorator registers an adapter class with the ExchangeFactory,
    making it available for instantiation by name.

    Args:
        name: Exchange identifier (e.g., "binance", "coinbase", "kraken")

    Returns:
        Decorator function

    Example:
        >>> @register_adapter("myexchange")
        >>> class MyExchangeAdapter(BaseExchangeAdapter):
        ...     exchange_name = "myexchange"
        ...
        ...     async def connect(self) -> bool:
        ...         # Implementation
        ...         pass
    """

    def decorator(cls: Type[T]) -> Type[T]:
        """Inner decorator that registers the class."""
        if not issubclass(cls, BaseExchangeAdapter):
            raise TypeError(f"Adapter class {cls.__name__} must inherit from BaseExchangeAdapter")

        # Register with normalized name
        normalized_name = name.lower()
        _adapter_registry[normalized_name] = cls

        # Also register by class name (without "Adapter" suffix)
        class_name = cls.__name__.lower()
        if class_name.endswith("adapter"):
            short_name = class_name[:-7]  # Remove "adapter" suffix
            if short_name and short_name not in _adapter_registry:
                _adapter_registry[short_name] = cls

        return cls

    return decorator


def get_registered_adapters() -> Dict[str, str]:
    """Get a dictionary of registered adapter names and their class names.

    Returns:
        Dictionary mapping adapter names to class names
    """
    return {name: cls.__name__ for name, cls in _adapter_registry.items()}


def is_adapter_registered(name: str) -> bool:
    """Check if an adapter is registered.

    Args:
        name: Exchange name to check

    Returns:
        True if adapter is registered, False otherwise
    """
    return name.lower() in _adapter_registry


class ExchangeFactory:
    """Factory class for creating exchange adapters.

    This factory provides a unified interface for instantiating exchange
    adapters by name, with support for configuration loading from various
    sources (direct parameters, dictionaries, or environment variables).

    The factory maintains a registry of available adapters and handles
    adapter instantiation with appropriate configuration.

    Example:
        >>> # Create with direct parameters
        >>> adapter = ExchangeFactory.create(
        ...     exchange="binance",
        ...     api_key="key",
        ...     api_secret="secret",
        ...     sandbox=True
        ... )
        >>>
        >>> # Create from configuration dict
        >>> config = {
        ...     "exchange": "coinbase",
        ...     "api_key": "key",
        ...     "api_secret": "secret",
        ...     "passphrase": "pass",
        ...     "sandbox": False
        ... }
        >>> adapter = ExchangeFactory.create_from_config(config)
        >>>
        >>> # Create from environment variables
        >>> # Expects: BINANCE_API_KEY, BINANCE_API_SECRET, etc.
        >>> adapter = ExchangeFactory.create_from_env("binance")
    """

    @staticmethod
    def create(exchange: str, **kwargs) -> BaseExchangeAdapter:
        """Create an exchange adapter by name.

        Args:
            exchange: Exchange identifier (e.g., "binance", "coinbase")
            **kwargs: Adapter-specific configuration parameters
                - api_key: API key for authentication
                - api_secret: API secret for authentication
                - sandbox: Use sandbox/testnet (default: True)
                - rate_limit_per_second: Request rate limit (default: 10.0)
                - base_url: Override base URL
                - auth_type: Authentication type ("hmac", "rsa", "ed25519")
                - passphrase: Passphrase for exchanges that require it
                - private_key: Private key for RSA/Ed25519 signing
                - retry_config: Custom RetryConfig instance

        Returns:
            Instantiated exchange adapter

        Raises:
            ValueError: If exchange is not registered
            TypeError: If required parameters are missing

        Example:
            >>> adapter = ExchangeFactory.create(
            ...     exchange="binance",
            ...     api_key="your_key",
            ...     api_secret="your_secret",
            ...     sandbox=True
            ... )
        """
        exchange_name = exchange.lower()

        if exchange_name not in _adapter_registry:
            available = ", ".join(sorted(_adapter_registry.keys()))
            raise ValueError(
                f"Unknown exchange: '{exchange}'. " f"Available exchanges: {available}"
            )

        adapter_class = _adapter_registry[exchange_name]

        # Validate required parameters
        required_params = ["api_key", "api_secret"]
        missing = [p for p in required_params if p not in kwargs]
        if missing:
            raise TypeError(f"Missing required parameters for {exchange}: {', '.join(missing)}")

        return adapter_class(**kwargs)

    @staticmethod
    def create_from_config(config: Dict[str, Any]) -> BaseExchangeAdapter:
        """Create an exchange adapter from a configuration dictionary.

        Args:
            config: Configuration dictionary containing:
                - exchange (required): Exchange name
                - api_key (required): API key
                - api_secret (required): API secret
                - Plus any other adapter-specific options

        Returns:
            Instantiated exchange adapter

        Raises:
            ValueError: If config is missing required fields

        Example:
            >>> config = {
            ...     "exchange": "binance",
            ...     "api_key": "your_key",
            ...     "api_secret": "your_secret",
            ...     "sandbox": True,
            ...     "rate_limit_per_second": 20.0
            ... }
            >>> adapter = ExchangeFactory.create_from_config(config)
        """
        if not isinstance(config, dict):
            raise TypeError("Config must be a dictionary")

        required_keys = ["exchange", "api_key", "api_secret"]
        missing = [key for key in required_keys if key not in config]
        if missing:
            raise ValueError(f"Config missing required keys: {', '.join(missing)}")

        # Extract exchange name and remove from config
        exchange = config.pop("exchange")

        # Handle retry config
        if "retry_config" in config and isinstance(config["retry_config"], dict):
            from src.adapters.base_adapter import RetryConfig

            config["retry_config"] = RetryConfig(**config["retry_config"])

        return ExchangeFactory.create(exchange, **config)

    @staticmethod
    def create_from_env(
        exchange: str, prefix: Optional[str] = None, env_mapping: Optional[Dict[str, str]] = None
    ) -> BaseExchangeAdapter:
        """Create an exchange adapter from environment variables.

        Args:
            exchange: Exchange identifier
            prefix: Environment variable prefix (default: exchange name upper)
            env_mapping: Custom mapping of config keys to env variable names

        Returns:
            Instantiated exchange adapter

        Raises:
            ValueError: If required environment variables are missing

        Example:
            >>> # Expects BINANCE_API_KEY and BINANCE_API_SECRET
            >>> adapter = ExchangeFactory.create_from_env("binance")
            >>>
            >>> # With custom prefix
            >>> adapter = ExchangeFactory.create_from_env("binance", prefix="TRADING")
            >>> # Expects TRADING_API_KEY and TRADING_API_SECRET
            >>>
            >>> # With custom mapping
            >>> mapping = {
            ...     "api_key": "MY_BINANCE_KEY",
            ...     "api_secret": "MY_BINANCE_SECRET"
            ... }
            >>> adapter = ExchangeFactory.create_from_env("binance", env_mapping=mapping)
        """
        exchange_upper = exchange.upper()
        prefix = (prefix or exchange_upper).rstrip("_")

        # Default environment variable mapping
        default_mapping = {
            "api_key": f"{prefix}_API_KEY",
            "api_secret": f"{prefix}_API_SECRET",
            "passphrase": f"{prefix}_PASSPHRASE",
            "private_key": f"{prefix}_PRIVATE_KEY",
            "sandbox": f"{prefix}_SANDBOX",
        }

        # Use custom mapping if provided
        if env_mapping:
            default_mapping.update(env_mapping)

        # Build config from environment
        config = {"exchange": exchange}

        # Required parameters
        for key in ["api_key", "api_secret"]:
            env_var = default_mapping[key]
            value = os.environ.get(env_var)
            if not value:
                raise ValueError(f"Missing required environment variable: {env_var}")
            config[key] = value

        # Optional parameters
        for key in ["passphrase", "private_key"]:
            env_var = default_mapping.get(key)
            if env_var:
                value = os.environ.get(env_var)
                if value:
                    config[key] = value

        # Boolean parameters
        sandbox_var = default_mapping.get("sandbox")
        if sandbox_var and sandbox_var in os.environ:
            config["sandbox"] = os.environ[sandbox_var].lower() in ("true", "1", "yes", "on")

        # Numeric parameters
        rate_limit_var = os.environ.get(f"{prefix}_RATE_LIMIT")
        if rate_limit_var:
            try:
                config["rate_limit_per_second"] = float(rate_limit_var)
            except ValueError:
                pass

        return ExchangeFactory.create_from_config(config)

    @staticmethod
    def get_available_exchanges() -> list:
        """Get list of available/registered exchanges.

        Returns:
            List of exchange names
        """
        return sorted(_adapter_registry.keys())

    @staticmethod
    def get_adapter_class(exchange: str) -> Type[BaseExchangeAdapter]:
        """Get the adapter class for an exchange without instantiating.

        Args:
            exchange: Exchange identifier

        Returns:
            Adapter class

        Raises:
            ValueError: If exchange is not registered
        """
        exchange_name = exchange.lower()
        if exchange_name not in _adapter_registry:
            available = ", ".join(sorted(_adapter_registry.keys()))
            raise ValueError(f"Unknown exchange: '{exchange}'. " f"Available: {available}")
        return _adapter_registry[exchange_name]


# Convenience functions
def create_adapter(exchange: str, **kwargs) -> BaseExchangeAdapter:
    """Convenience function to create an exchange adapter.

    This is a shortcut for ExchangeFactory.create().

    Args:
        exchange: Exchange identifier
        **kwargs: Adapter configuration parameters

    Returns:
        Instantiated exchange adapter

    Example:
        >>> from src.adapters import create_adapter
        >>> adapter = create_adapter("binance", api_key="key", api_secret="secret")
    """
    return ExchangeFactory.create(exchange, **kwargs)


def create_adapter_from_config(config: Dict[str, Any]) -> BaseExchangeAdapter:
    """Convenience function to create an adapter from config.

    This is a shortcut for ExchangeFactory.create_from_config().

    Args:
        config: Configuration dictionary

    Returns:
        Instantiated exchange adapter
    """
    return ExchangeFactory.create_from_config(config)


def create_adapter_from_env(exchange: str, prefix: Optional[str] = None) -> BaseExchangeAdapter:
    """Convenience function to create an adapter from environment.

    This is a shortcut for ExchangeFactory.create_from_env().

    Args:
        exchange: Exchange identifier
        prefix: Environment variable prefix

    Returns:
        Instantiated exchange adapter
    """
    return ExchangeFactory.create_from_env(exchange, prefix)


# Public API
__all__ = [
    # Factory
    "ExchangeFactory",
    "register_adapter",
    "get_registered_adapters",
    "is_adapter_registered",
    # Convenience functions
    "create_adapter",
    "create_adapter_from_config",
    "create_adapter_from_env",
    # Exchange Adapters
    "BybitAdapter",
    # Base classes
    "BaseExchangeAdapter",
    "RetryConfig",
    # Data classes
    "Order",
    "OrderType",
    "OrderSide",
    "OrderStatus",
    "TimeInForce",
    "Ticker",
    "Position",
    "Balance",
    "Candle",
    "OrderBook",
    "AccountInfo",
    # Exceptions
    "ExchangeError",
    "ExchangeConnectionError",
    "AuthenticationError",
    "RateLimitError",
    "OrderError",
    "InsufficientFundsError",
    "InvalidSymbolError",
    "MarketClosedError",
    "WebSocketError",
    "DataValidationError",
    # Auth
    "AuthConfig",
    "RequestSigner",
    "HMACSigner",
    "BinanceHMACSigner",
    "CoinbaseHMACSigner",
    "RSASigner",
    "Ed25519Signer",
    "ClockSkewManager",
    "create_signer",
]


# Import and register exchange adapters
try:
    from src.adapters.bybit import BybitAdapter

    register_adapter("bybit")(BybitAdapter)
except ImportError:
    pass  # Bybit adapter not available
