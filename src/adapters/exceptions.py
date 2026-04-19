"""Custom exceptions for exchange adapters.

This module defines a hierarchy of exceptions for handling various error
cenarios that can occur when interacting with cryptocurrency exchanges.
"""

from typing import Optional, Dict, Any


class ExchangeError(Exception):
    """Base exception for all exchange-related errors.

    Attributes:
        message: Human-readable error description
        exchange: Name of the exchange where error occurred
        error_code: Exchange-specific error code if available
        response_data: Raw response data from the exchange
    """

    def __init__(
        self,
        message: str,
        exchange: Optional[str] = None,
        error_code: Optional[str] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.exchange = exchange
        self.error_code = error_code
        self.response_data = response_data or {}

    def __str__(self) -> str:
        parts = [self.message]
        if self.exchange:
            parts.append(f"(Exchange: {self.exchange})")
        if self.error_code:
            parts.append(f"[Code: {self.error_code}]")
        return " ".join(parts)


class ExchangeConnectionError(ExchangeError):
    """Exception raised when connection to exchange fails.

    This includes network errors, timeouts, DNS failures, and other
    transport-level issues that prevent establishing communication
    with the exchange API.

    Example:
        >>> try:
        ...     await adapter.connect()
        ... except ExchangeConnectionError as e:
        ...     logger.error(f"Cannot connect: {e}")
    """

    def __init__(
        self,
        message: str = "Failed to connect to exchange",
        exchange: Optional[str] = None,
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.retry_after = retry_after  # Seconds to wait before retry


class AuthenticationError(ExchangeError):
    """Exception raised when API authentication fails.

    This includes invalid API keys, expired signatures, insufficient
    permissions, or any other credential-related issues.

    Attributes:
        requires_new_key: Whether a new API key is required

    Example:
        >>> try:
        ...     await adapter.get_account()
        ... except AuthenticationError as e:
        ...     logger.error(f"Auth failed: {e}")
        ...     if e.requires_new_key:
        ...         notify_admin("API key needs rotation")
    """

    def __init__(
        self,
        message: str = "Authentication failed",
        exchange: Optional[str] = None,
        requires_new_key: bool = False,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.requires_new_key = requires_new_key


class RateLimitError(ExchangeError):
    """Exception raised when API rate limit is exceeded.

    Attributes:
        retry_after: Seconds to wait before retrying the request
        limit: Maximum requests allowed in the time window
        remaining: Number of requests remaining in current window
        reset_time: Unix timestamp when the rate limit resets

    Example:
        >>> try:
        ...     await adapter.place_order(order)
        ... except RateLimitError as e:
        ...     await asyncio.sleep(e.retry_after)
        ...     await adapter.place_order(order)  # Retry
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        exchange: Optional[str] = None,
        retry_after: Optional[float] = None,
        limit: Optional[int] = None,
        remaining: Optional[int] = None,
        reset_time: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.retry_after = retry_after or 60.0
        self.limit = limit
        self.remaining = remaining
        self.reset_time = reset_time


class OrderError(ExchangeError):
    """Exception raised when order placement or management fails.

    This includes invalid order parameters, market not available,
    minimum size not met, and other order-specific issues.

    Attributes:
        order_id: ID of the order that caused the error
        symbol: Trading symbol involved
        rejection_reason: Specific reason for order rejection

    Example:
        >>> try:
        ...     await adapter.place_order(order)
        ... except OrderError as e:
        ...     if "minimum" in e.rejection_reason.lower():
        ...         # Adjust order size
        ...         pass
    """

    def __init__(
        self,
        message: str = "Order operation failed",
        exchange: Optional[str] = None,
        order_id: Optional[str] = None,
        symbol: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.order_id = order_id
        self.symbol = symbol
        self.rejection_reason = rejection_reason


class InsufficientFundsError(OrderError):
    """Exception raised when account has insufficient funds for operation.

    Attributes:
        required: Amount required for the operation
        available: Amount actually available
        asset: Currency/asset that is insufficient
        shortfall: Difference between required and available

    Example:
        >>> try:
        ...     await adapter.place_order(large_order)
        ... except InsufficientFundsError as e:
        ...     logger.warning(
        ...         f"Need {e.required} {e.asset}, "
        ...         f"have {e.available}"
        ...     )
    """

    def __init__(
        self,
        message: str = "Insufficient funds",
        exchange: Optional[str] = None,
        required: Optional[float] = None,
        available: Optional[float] = None,
        asset: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.required = required
        self.available = available
        self.asset = asset
        self.shortfall = (required - available) if required and available else None

        # Enhance message with details
        if asset and (required or available):
            details = f"Asset: {asset}"
            if required:
                details += f", Required: {required}"
            if available:
                details += f", Available: {available}"
            self.message = f"{message} ({details})"


class InvalidSymbolError(ExchangeError):
    """Exception raised when trading symbol is invalid or unavailable.

    Attributes:
        symbol: The invalid symbol
        suggestions: List of similar valid symbols (if available)
    """

    def __init__(
        self,
        message: str = "Invalid trading symbol",
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        suggestions: Optional[list] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.symbol = symbol
        self.suggestions = suggestions or []

        if symbol:
            self.message = f"{message}: {symbol}"


class MarketClosedError(ExchangeError):
    """Exception raised when market is closed or in maintenance.

    Attributes:
        symbol: Trading symbol that is closed
        reopen_time: Expected reopen time (if known)
    """

    def __init__(
        self,
        message: str = "Market is closed",
        exchange: Optional[str] = None,
        symbol: Optional[str] = None,
        reopen_time: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, exchange, **kwargs)
        self.symbol = symbol
        self.reopen_time = reopen_time


class WebSocketError(ExchangeError):
    """Exception raised for WebSocket-related errors."""

    pass


class DataValidationError(ExchangeError):
    """Exception raised when response data validation fails."""

    pass
