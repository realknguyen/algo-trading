"""Tests for hardened application settings."""

from pydantic import SecretStr

from config.settings import (
    AlpacaConfig,
    BinanceConfig,
    CoinbaseConfig,
    DatabaseConfig,
    KrakenConfig,
    resolve_database_url,
)


class TestSettingsSecurity:
    """Security-oriented settings tests."""

    def test_secret_fields_use_secret_types(self):
        """Credential-bearing fields should use secret-aware types."""
        database = DatabaseConfig(password="db-secret")
        binance = BinanceConfig(api_key="binance-key", api_secret="binance-secret")
        kraken = KrakenConfig(api_key="kraken-key", api_secret="kraken-secret")
        coinbase = CoinbaseConfig(
            api_key="coinbase-key",
            api_secret="coinbase-secret",
            passphrase="coinbase-passphrase",
        )
        alpaca = AlpacaConfig(api_key="alpaca-key", api_secret="alpaca-secret")

        assert isinstance(database.password, SecretStr)
        assert isinstance(binance.api_key, SecretStr)
        assert isinstance(binance.api_secret, SecretStr)
        assert isinstance(kraken.api_key, SecretStr)
        assert isinstance(kraken.api_secret, SecretStr)
        assert isinstance(coinbase.api_key, SecretStr)
        assert isinstance(coinbase.api_secret, SecretStr)
        assert isinstance(coinbase.passphrase, SecretStr)
        assert isinstance(alpaca.api_key, SecretStr)
        assert isinstance(alpaca.api_secret, SecretStr)

    def test_database_url_escapes_special_characters(self):
        """Database URLs should be built with escaping, not raw interpolation."""
        database = DatabaseConfig(
            host="db.example",
            port=5432,
            name="trading",
            user="trader",
            password="pa:ss/word",
        )

        assert database.url.startswith("postgresql://trader:")
        assert "pa:ss/word" not in database.url
        assert "%3A" in database.url or "%2F" in database.url

    def test_resolve_database_url_prefers_explicit_database_url(self):
        """An explicit DATABASE_URL should win over derived configuration."""
        url = resolve_database_url({"DATABASE_URL": "postgresql://example/test"})

        assert url == "postgresql://example/test"
