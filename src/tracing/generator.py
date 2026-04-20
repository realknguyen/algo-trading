"""
ID Generation for Distributed Tracing.

Generates unique identifiers for requests, traces, and spans.
Uses UUID7 (preferred) with nanoid fallback for URL-safe IDs.
"""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import datetime
from typing import Optional


class RequestIDGenerator:
    """
    Generates unique identifiers for distributed tracing.

    Supports:
    - UUID7 (preferred): Time-ordered, lexicographically sortable
    - Nanoid: URL-safe, compact, collision-resistant
    - Standard UUID4: Random-based fallback
    """

    # Nanoid alphabet (URL-safe, unambiguous)
    _NANOID_ALPHABET = string.ascii_letters + string.digits + "_-"
    _NANOID_SIZE = 21  # Default nanoid size (~149 billion years needed for 1% collision)

    # UUID version preference
    _PREFER_UUID7 = True
    _UUID7_AVAILABLE: Optional[bool] = None

    @classmethod
    def _check_uuid7(cls) -> bool:
        """Check if UUID7 is available (Python 3.14+ or uuid-utils)."""
        if cls._UUID7_AVAILABLE is not None:
            return cls._UUID7_AVAILABLE

        try:
            # Try standard library UUID7 (Python 3.14+)
            uuid.uuid7()
            cls._UUID7_AVAILABLE = True
        except AttributeError:
            # Try uuid-utils package
            try:
                import uuid_utils

                uuid_utils.uuid7()
                cls._UUID7_AVAILABLE = True
            except ImportError:
                cls._UUID7_AVAILABLE = False

        return cls._UUID7_AVAILABLE

    @classmethod
    def generate_uuid7(cls) -> uuid.UUID:
        """Generate a UUID7 (time-ordered, lexicographically sortable)."""
        try:
            return uuid.uuid7()
        except AttributeError:
            # Fallback to uuid-utils
            try:
                import uuid_utils

                return uuid_utils.uuid7()
            except ImportError:
                raise RuntimeError(
                    "UUID7 not available. Install Python 3.14+ or `pip install uuid-utils`"
                )

    @classmethod
    def generate_nanoid(cls, size: int = _NANOID_SIZE) -> str:
        """
        Generate a URL-safe nanoid.

        Nanoid provides:
        - Compact size (21 chars default)
        - URL-safe alphabet
        - Cryptographically secure random generation
        - No dependencies (pure Python implementation)
        """
        alphabet = cls._NANOID_ALPHABET
        return "".join(secrets.choice(alphabet) for _ in range(size))

    @classmethod
    def generate_uuid4(cls) -> uuid.UUID:
        """Generate a standard UUID4 (random)."""
        return uuid.uuid4()

    @classmethod
    def generate_request_id(cls, use_nanoid: bool = False) -> str:
        """
        Generate a unique request ID.

        Args:
            use_nanoid: If True, use nanoid instead of UUID

        Returns:
            Unique request identifier string
        """
        if use_nanoid:
            return cls.generate_nanoid()

        if cls._PREFER_UUID7 and cls._check_uuid7():
            return str(cls.generate_uuid7())

        return str(cls.generate_uuid4())

    @classmethod
    def generate_trace_id(cls, use_nanoid: bool = False) -> str:
        """
        Generate a distributed trace ID.

        Trace IDs are typically 32 hex chars (16 bytes) for W3C compatibility.

        Args:
            use_nanoid: If True, use nanoid instead of hex

        Returns:
            Trace identifier string
        """
        if use_nanoid:
            # Generate 32-char nanoid for W3C compatibility
            return cls.generate_nanoid(32)

        # 32 hex chars = 16 bytes = 128 bits (W3C standard)
        return secrets.token_hex(16)

    @classmethod
    def generate_span_id(cls, use_nanoid: bool = False) -> str:
        """
        Generate a span ID for sub-operations.

        Span IDs are typically 16 hex chars (8 bytes) for W3C compatibility.

        Args:
            use_nanoid: If True, use nanoid instead of hex

        Returns:
            Span identifier string
        """
        if use_nanoid:
            # Generate 16-char nanoid for W3C compatibility
            return cls.generate_nanoid(16)

        # 16 hex chars = 8 bytes = 64 bits (W3C standard)
        return secrets.token_hex(8)

    @classmethod
    def generate_timestamp_id(cls) -> str:
        """
        Generate a time-ordered ID combining timestamp and random.

        Format: <timestamp>_<random>
        Example: 20240115_120530_a1b2c3d4

        Returns:
            Time-ordered identifier string
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        random_suffix = secrets.token_hex(4)
        return f"{timestamp}_{random_suffix}"

    @classmethod
    def parse_trace_id(cls, trace_id: str) -> dict:
        """
        Parse trace ID to determine type and extract info if possible.

        Args:
            trace_id: The trace ID string

        Returns:
            Dict with type info and any extracted metadata
        """
        result = {
            "type": "unknown",
            "length": len(trace_id),
            "is_hex": all(c in string.hexdigits for c in trace_id),
        }

        if len(trace_id) == 32 and result["is_hex"]:
            result["type"] = "w3c_trace"
        elif len(trace_id) == 36 and trace_id.count("-") == 4:
            result["type"] = "uuid"
            try:
                uuid.UUID(trace_id)
                result["valid_uuid"] = True
            except ValueError:
                result["valid_uuid"] = False
        elif len(trace_id) == cls._NANOID_SIZE and all(c in cls._NANOID_ALPHABET for c in trace_id):
            result["type"] = "nanoid"

        return result


class IDFormatter:
    """
    Format IDs for various output contexts.
    """

    @staticmethod
    def to_hex(id_str: str) -> str:
        """Convert any ID to hex format (for W3C compatibility)."""
        if len(id_str) == 32 and all(c in string.hexdigits for c in id_str):
            return id_str.lower()

        # Encode string to bytes and convert to hex
        return id_str.encode("utf-8").hex()[:32]

    @staticmethod
    def to_compact(id_str: str) -> str:
        """Convert UUID to compact form (no dashes)."""
        return id_str.replace("-", "")

    @staticmethod
    def to_uuid(compact_hex: str) -> str:
        """Convert compact hex to UUID format."""
        if len(compact_hex) == 32:
            return f"{compact_hex[:8]}-{compact_hex[8:12]}-{compact_hex[12:16]}-{compact_hex[16:20]}-{compact_hex[20:]}"
        return compact_hex


# Convenience functions for module-level access
def generate_request_id(use_nanoid: bool = False) -> str:
    """Generate a unique request ID."""
    return RequestIDGenerator.generate_request_id(use_nanoid)


def generate_trace_id(use_nanoid: bool = False) -> str:
    """Generate a distributed trace ID."""
    return RequestIDGenerator.generate_trace_id(use_nanoid)


def generate_span_id(use_nanoid: bool = False) -> str:
    """Generate a span ID."""
    return RequestIDGenerator.generate_span_id(use_nanoid)


def generate_nanoid(size: int = 21) -> str:
    """Generate a nanoid."""
    return RequestIDGenerator.generate_nanoid(size)
