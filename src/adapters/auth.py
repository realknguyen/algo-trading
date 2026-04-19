"""Authentication modules for exchange API requests.

This module provides comprehensive authentication support for cryptocurrency
exchange APIs, including HMAC signatures, RSA signatures, and Ed25519 signatures.
It also includes utilities for timestamp generation with clock skew correction.
"""

import base64
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, Union
from urllib.parse import urlencode, urlparse

# Optional imports for advanced signature schemes
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519
    from cryptography.exceptions import InvalidSignature

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False

from src.adapters.exceptions import AuthenticationError


@dataclass
class AuthConfig:
    """Configuration for authentication.

    Attributes:
        api_key: API key for the exchange
        api_secret: API secret (used for HMAC)
        passphrase: Optional passphrase (required by some exchanges like Coinbase)
        private_key: Private key for RSA/Ed25519 signatures (PEM format or bytes)
        key_id: Key identifier for some exchanges
        clock_skew_ms: Clock skew correction in milliseconds
    """

    api_key: str
    api_secret: Optional[str] = None
    passphrase: Optional[str] = None
    private_key: Optional[Union[str, bytes]] = None
    key_id: Optional[str] = None
    clock_skew_ms: int = 0


class ClockSkewManager:
    """Manages clock skew correction between client and exchange servers.

    Many exchanges require timestamps within a small window (e.g., 5 seconds).
    This class helps synchronize timestamps by tracking the offset between
    local time and exchange server time.

    Example:
        >>> clock = ClockSkewManager()
        >>> # After getting server time from exchange
        >>> clock.update_skew(server_timestamp)
        >>> timestamp = clock.get_timestamp()  # Corrected timestamp
    """

    def __init__(self, default_skew_ms: int = 0):
        self._skew_ms = default_skew_ms
        self._last_sync: Optional[float] = None
        self._sync_count = 0

    @property
    def skew_ms(self) -> int:
        """Current clock skew in milliseconds."""
        return self._skew_ms

    @property
    def skew_seconds(self) -> float:
        """Current clock skew in seconds."""
        return self._skew_ms / 1000.0

    def update_skew(self, server_timestamp_ms: Union[int, float]) -> None:
        """Update clock skew based on server timestamp.

        Args:
            server_timestamp_ms: Current server timestamp in milliseconds
        """
        local_timestamp_ms = time.time() * 1000
        self._skew_ms = int(server_timestamp_ms - local_timestamp_ms)
        self._last_sync = time.time()
        self._sync_count += 1

    def get_timestamp_ms(self) -> int:
        """Get current timestamp corrected for clock skew (milliseconds).

        Returns:
            Unix timestamp in milliseconds adjusted for skew
        """
        return int(time.time() * 1000) + self._skew_ms

    def get_timestamp(self) -> float:
        """Get current timestamp corrected for clock skew (seconds).

        Returns:
            Unix timestamp in seconds adjusted for skew
        """
        return time.time() + self.skew_seconds

    def get_timestamp_str(self) -> str:
        """Get timestamp as string (seconds with decimal).

        Returns:
            Unix timestamp as string (e.g., "1234567890.123")
        """
        return f"{self.get_timestamp():.3f}"

    def is_synced(self) -> bool:
        """Check if clock has been synchronized at least once."""
        return self._sync_count > 0

    def time_since_sync(self) -> Optional[float]:
        """Get seconds since last synchronization."""
        if self._last_sync is None:
            return None
        return time.time() - self._last_sync


class RequestSigner(ABC):
    """Abstract base class for request signing implementations.

    Different exchanges use different authentication schemes. This base class
    defines the interface that all signers must implement.
    """

    def __init__(self, config: AuthConfig):
        self.config = config
        self.clock = ClockSkewManager(config.clock_skew_ms)

    @abstractmethod
    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign an API request and return authentication headers.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data
            headers: Existing headers to include in signature

        Returns:
            Dictionary of authentication headers to add to request
        """
        pass

    @abstractmethod
    def get_timestamp_header(self) -> Dict[str, str]:
        """Get timestamp header(s) for the exchange.

        Returns:
            Dictionary containing timestamp header(s)
        """
        pass

    def sync_clock(self, server_timestamp_ms: Union[int, float]) -> None:
        """Synchronize clock with exchange server time.

        Args:
            server_timestamp_ms: Server timestamp in milliseconds
        """
        self.clock.update_skew(server_timestamp_ms)


class HMACSigner(RequestSigner):
    """HMAC-based request signer for exchanges like Binance, Coinbase, etc.

    This signer generates HMAC-SHA256 (or other algorithm) signatures
    from the request components and API secret.

    Example:
        >>> config = AuthConfig(api_key="my_key", api_secret="my_secret")
        >>> signer = HMACSigner(config)
        >>> headers = signer.sign_request("GET", "/api/v3/account")
    """

    def __init__(
        self, config: AuthConfig, algorithm: str = "sha256", signature_encoding: str = "hex"
    ):
        """Initialize HMAC signer.

        Args:
            config: Authentication configuration
            algorithm: Hash algorithm ("sha256", "sha384", "sha512")
            signature_encoding: Signature encoding ("hex", "base64")
        """
        super().__init__(config)
        self.algorithm = algorithm.lower()
        self.signature_encoding = signature_encoding.lower()

        if not config.api_secret:
            raise AuthenticationError("API secret is required for HMAC signing")

    def _get_hash_function(self):
        """Get hash function based on algorithm name."""
        algorithms = {
            "sha256": hashlib.sha256,
            "sha384": hashlib.sha384,
            "sha512": hashlib.sha512,
            "sha1": hashlib.sha1,
        }
        return algorithms.get(self.algorithm, hashlib.sha256)

    def _encode_signature(self, signature: bytes) -> str:
        """Encode signature to string format."""
        if self.signature_encoding == "hex":
            return signature.hex()
        elif self.signature_encoding == "base64":
            return base64.b64encode(signature).decode("utf-8")
        else:
            return signature.hex()

    def _create_signature(self, message: str) -> str:
        """Create HMAC signature from message."""
        if not self.config.api_secret:
            raise AuthenticationError("API secret not configured")

        secret = self.config.api_secret.encode("utf-8")
        message_bytes = message.encode("utf-8")

        signature = hmac.new(secret, message_bytes, self._get_hash_function()).digest()

        return self._encode_signature(signature)

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign request using HMAC.

        Creates a signature from the request components. The exact format
        varies by exchange, so this base implementation provides a common
        approach that can be overridden for specific exchanges.

        Returns:
            Dictionary with signature, timestamp, and API key headers
        """
        timestamp = self.clock.get_timestamp_str()

        # Build message to sign (override in subclasses for exchange-specific formats)
        message_parts = [timestamp, method.upper(), endpoint]

        # Add query string if params exist
        if params:
            query_string = urlencode(sorted(params.items()))
            message_parts.append(query_string)

        # Add body if data exists
        if data:
            body = json.dumps(data, separators=(",", ":"))
            message_parts.append(body)

        message = "".join(message_parts)
        signature = self._create_signature(message)

        return {
            "X-API-KEY": self.config.api_key,
            "X-SIGNATURE": signature,
            "X-TIMESTAMP": timestamp,
        }

    def get_timestamp_header(self) -> Dict[str, str]:
        """Get timestamp header."""
        return {"X-TIMESTAMP": self.clock.get_timestamp_str()}


class BinanceHMACSigner(HMACSigner):
    """HMAC signer specifically for Binance-style authentication.

    Binance uses a specific signature format where query parameters
    are URL-encoded and signed with HMAC-SHA256.
    """

    def __init__(self, config: AuthConfig):
        super().__init__(config, algorithm="sha256", signature_encoding="hex")

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign request in Binance format.

        Binance format: query_string = param1=value1&param2=value2&timestamp=...
        signature = HMAC_SHA256(query_string)
        """
        all_params = dict(params) if params else {}

        # Add timestamp
        all_params["timestamp"] = self.clock.get_timestamp_ms()

        # Add recvWindow if not present
        if "recvWindow" not in all_params:
            all_params["recvWindow"] = 5000

        # Create query string
        query_string = urlencode(sorted(all_params.items()))

        # Create signature
        signature = self._create_signature(query_string)

        return {
            "X-MBX-APIKEY": self.config.api_key,
            "signature": signature,
            "timestamp": str(all_params["timestamp"]),
            "recvWindow": str(all_params["recvWindow"]),
        }


class CoinbaseHMACSigner(HMACSigner):
    """HMAC signer for Coinbase Pro / Advanced Trade.

    Coinbase uses a specific format: timestamp + method + endpoint + body
    """

    def __init__(self, config: AuthConfig):
        super().__init__(config, algorithm="sha256", signature_encoding="base64")

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign request in Coinbase format.

        Coinbase format: prehash = timestamp + method + endpoint + body
        signature = base64(hmac_sha256(prehash))
        """
        timestamp = str(self.clock.get_timestamp())

        # Build prehash string
        prehash_parts = [timestamp, method.upper(), endpoint]

        if data:
            body = json.dumps(data)
            prehash_parts.append(body)

        prehash = "".join(prehash_parts)

        # Coinbase API secrets are base64-encoded
        secret_bytes = base64.b64decode(self.config.api_secret)
        signature = hmac.new(secret_bytes, prehash.encode("utf-8"), hashlib.sha256).digest()

        signature_b64 = base64.b64encode(signature).decode("utf-8")

        result = {
            "CB-ACCESS-KEY": self.config.api_key,
            "CB-ACCESS-SIGN": signature_b64,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

        if self.config.passphrase:
            result["CB-ACCESS-PASSPHRASE"] = self.config.passphrase

        return result


class RSASigner(RequestSigner):
    """RSA-based request signer for exchanges supporting RSA signatures.

    Some advanced exchanges (like certain institutional APIs) support or
    require RSA signatures for enhanced security.

    Note: Requires the 'cryptography' package to be installed.

    Example:
        >>> config = AuthConfig(
        ...     api_key="my_key",
        ...     private_key=open("private.pem").read()
        ... )
        >>> signer = RSASigner(config)
    """

    def __init__(
        self, config: AuthConfig, hash_algorithm: str = "sha256", padding_scheme: str = "pkcs1v15"
    ):
        """Initialize RSA signer.

        Args:
            config: Authentication configuration with private_key
            hash_algorithm: Hash algorithm for signature
            padding_scheme: Padding scheme ("pkcs1v15" or "pss")
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "RSA signing requires 'cryptography' package. "
                "Install with: pip install cryptography"
            )

        super().__init__(config)

        if not config.private_key:
            raise AuthenticationError("Private key is required for RSA signing")

        self.hash_algorithm = hash_algorithm
        self.padding_scheme = padding_scheme

        # Load private key
        key_data = config.private_key
        if isinstance(key_data, str):
            key_data = key_data.encode("utf-8")

        self._private_key = serialization.load_pem_private_key(key_data, password=None)

    def _get_hash(self):
        """Get hash algorithm instance."""
        algorithms = {
            "sha256": hashes.SHA256(),
            "sha384": hashes.SHA384(),
            "sha512": hashes.SHA512(),
        }
        return algorithms.get(self.hash_algorithm, hashes.SHA256())

    def _get_padding(self):
        """Get padding instance."""
        if self.padding_scheme == "pss":
            return padding.PSS(mgf=padding.MGF1(self._get_hash()), salt_length=padding.PSS.AUTO)
        return padding.PKCS1v15()

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign request using RSA private key."""
        timestamp = self.clock.get_timestamp_str()

        # Build message
        message_parts = [timestamp, method.upper(), endpoint]

        if params:
            query_string = urlencode(sorted(params.items()))
            message_parts.append(query_string)

        if data:
            body = json.dumps(data, separators=(",", ":"))
            message_parts.append(body)

        message = "".join(message_parts).encode("utf-8")

        # Create signature
        signature = self._private_key.sign(message, self._get_padding(), self._get_hash())

        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "X-API-KEY": self.config.api_key,
            "X-SIGNATURE": signature_b64,
            "X-TIMESTAMP": timestamp,
            "X-SIGNATURE-ALGORITHM": f"RSA-{self.hash_algorithm.upper()}",
        }

    def get_timestamp_header(self) -> Dict[str, str]:
        """Get timestamp header."""
        return {"X-TIMESTAMP": self.clock.get_timestamp_str()}


class Ed25519Signer(RequestSigner):
    """Ed25519-based request signer for modern exchange APIs.

    Ed25519 provides fast, secure signatures with small key sizes.
    Used by some newer exchanges and institutional APIs.

    Note: Requires the 'cryptography' package to be installed.
    """

    def __init__(self, config: AuthConfig):
        """Initialize Ed25519 signer.

        Args:
            config: Authentication configuration with private_key
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "Ed25519 signing requires 'cryptography' package. "
                "Install with: pip install cryptography"
            )

        super().__init__(config)

        if not config.private_key:
            raise AuthenticationError("Private key is required for Ed25519 signing")

        # Load private key
        key_data = config.private_key
        if isinstance(key_data, str):
            key_data = key_data.encode("utf-8")

        self._private_key = serialization.load_pem_private_key(key_data, password=None)

        if not isinstance(self._private_key, ed25519.Ed25519PrivateKey):
            raise AuthenticationError("Private key is not a valid Ed25519 key")

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Sign request using Ed25519 private key."""
        timestamp = self.clock.get_timestamp_str()

        # Build message (similar to RSA)
        message_parts = [timestamp, method.upper(), endpoint]

        if params:
            query_string = urlencode(sorted(params.items()))
            message_parts.append(query_string)

        if data:
            body = json.dumps(data, separators=(",", ":"))
            message_parts.append(body)

        message = "".join(message_parts).encode("utf-8")

        # Create signature
        signature = self._private_key.sign(message)
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "X-API-KEY": self.config.api_key,
            "X-SIGNATURE": signature_b64,
            "X-TIMESTAMP": timestamp,
            "X-SIGNATURE-ALGORITHM": "Ed25519",
        }

    def get_timestamp_header(self) -> Dict[str, str]:
        """Get timestamp header."""
        return {"X-TIMESTAMP": self.clock.get_timestamp_str()}


def create_signer(auth_type: str, config: AuthConfig, **kwargs) -> RequestSigner:
    """Factory function to create appropriate signer based on type.

    Args:
        auth_type: Type of authentication ("hmac", "binance", "coinbase",
                   "rsa", "ed25519")
        config: Authentication configuration
        **kwargs: Additional arguments for specific signer types

    Returns:
        Configured RequestSigner instance

    Raises:
        ValueError: If auth_type is not recognized

    Example:
        >>> config = AuthConfig(api_key="key", api_secret="secret")
        >>> signer = create_signer("binance", config)
    """
    auth_type = auth_type.lower()

    signers = {
        "hmac": HMACSigner,
        "binance": BinanceHMACSigner,
        "coinbase": CoinbaseHMACSigner,
        "rsa": RSASigner,
        "ed25519": Ed25519Signer,
    }

    signer_class = signers.get(auth_type)
    if not signer_class:
        raise ValueError(f"Unknown auth type: {auth_type}. " f"Available: {list(signers.keys())}")

    return signer_class(config, **kwargs)
