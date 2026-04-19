"""Tests for hardened logging configuration."""

from config.settings import LoggingConfig
from log_config import (
    build_loguru_options,
    is_logging_configured,
    reset_logging_state,
    sanitize_for_logging,
    setup_logging,
)


class TestLogConfig:
    """Test cases for the canonical logging module."""

    def test_build_loguru_options_secure_defaults(self):
        """Default logging should not enable local-variable diagnostics."""
        config = LoggingConfig(file_path="logs/test.log")

        options = build_loguru_options(config, debug_diagnostics=False)

        assert options["backtrace"] is False
        assert options["diagnose"] is False

    def test_build_loguru_options_debug_mode(self):
        """Explicit debug diagnostics should opt in to deep tracebacks."""
        config = LoggingConfig(file_path="logs/test.log", debug_diagnostics=True)

        options = build_loguru_options(config, debug_diagnostics=True)

        assert options["backtrace"] is True
        assert options["diagnose"] is True

    def test_sanitize_for_logging_redacts_nested_secret_fields(self):
        """Sensitive fields should be redacted recursively before logging."""
        payload = {
            "api_secret": "top-secret",
            "nested": {
                "Authorization": "Bearer abc123",
                "public": "value",
            },
            "items": [
                {"private_key": "pem-data"},
                "passphrase=hunter2",
            ],
        }

        sanitized = sanitize_for_logging(payload)

        assert sanitized["api_secret"] == "***"
        assert sanitized["nested"]["Authorization"] == "***"
        assert sanitized["nested"]["public"] == "value"
        assert sanitized["items"][0]["private_key"] == "***"
        assert sanitized["items"][1] == "passphrase=***"

    def test_setup_logging_is_explicit(self, tmp_path):
        """Logging should only be configured when setup_logging is called."""
        reset_logging_state()
        assert is_logging_configured() is False

        config = LoggingConfig(file_path=str(tmp_path / "trading.log"))
        setup_logging(config=config, force=True)

        assert is_logging_configured() is True
