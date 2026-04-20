"""Configuration management for the trading system."""

import os
from typing import Mapping, Optional

from pydantic import Field, SecretStr, validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


def _secret_value(secret: SecretStr | None) -> str:
    """Safely unwrap a SecretStr into a plain string."""
    if secret is None:
        return ""
    return secret.get_secret_value()


def _build_database_url(
    *,
    drivername: str,
    host: str,
    port: int,
    name: str,
    user: str,
    password: SecretStr | None,
) -> str:
    """Build a SQLAlchemy URL with proper credential escaping."""
    rendered = URL.create(
        drivername=drivername,
        username=user,
        password=_secret_value(password) or None,
        host=host,
        port=port,
        database=name,
    )
    return rendered.render_as_string(hide_password=False)


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, description="Database port")
    name: str = Field(default="trading_db", description="Database name")
    user: str = Field(default="trader", description="Database user")
    password: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Database password",
    )

    @property
    def url(self) -> str:
        """Generate database URL."""
        return _build_database_url(
            drivername="postgresql",
            host=self.host,
            port=self.port,
            name=self.name,
            user=self.user,
            password=self.password,
        )

    @property
    def async_url(self) -> str:
        """Generate async database URL."""
        return _build_database_url(
            drivername="postgresql+asyncpg",
            host=self.host,
            port=self.port,
            name=self.name,
            user=self.user,
            password=self.password,
        )

    def to_safe_dict(self) -> dict[str, str | int]:
        """Return a redacted view suitable for logs and debugging."""
        return {
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "user": self.user,
            "password": "***" if _secret_value(self.password) else "",
        }


class BinanceConfig(BaseSettings):
    """Binance API configuration."""

    model_config = SettingsConfigDict(env_prefix="BINANCE_")

    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), description="Binance API key")
    api_secret: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Binance API secret",
    )
    testnet: bool = Field(default=True, description="Use testnet")
    base_url: str = Field(default="https://testnet.binance.vision", description="Base URL")
    ws_url: str = Field(default="wss://testnet.binance.vision/ws", description="WebSocket URL")


class KrakenConfig(BaseSettings):
    """Kraken API configuration."""

    model_config = SettingsConfigDict(env_prefix="KRAKEN_")

    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), description="Kraken API key")
    api_secret: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Kraken API secret",
    )
    sandbox: bool = Field(default=True, description="Use sandbox")


class CoinbaseConfig(BaseSettings):
    """Coinbase Pro API configuration."""

    model_config = SettingsConfigDict(env_prefix="COINBASE_")

    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Coinbase API key",
    )
    api_secret: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Coinbase API secret",
    )
    passphrase: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Coinbase API passphrase",
    )
    sandbox: bool = Field(default=True, description="Use sandbox")
    base_url: str = Field(
        default="https://api-public.sandbox.exchange.coinbase.com", description="Base URL"
    )


class AlpacaConfig(BaseSettings):
    """Alpaca API configuration."""

    model_config = SettingsConfigDict(env_prefix="ALPACA_")

    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), description="Alpaca API key")
    api_secret: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description="Alpaca API secret",
    )
    paper: bool = Field(default=True, description="Use paper trading")
    base_url: str = Field(default="https://paper-api.alpaca.markets", description="Base URL")


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    model_config = SettingsConfigDict(env_prefix="RISK_")

    max_position_size: float = Field(default=0.1, description="Max position size as % of portfolio")
    max_drawdown_pct: float = Field(default=10.0, description="Max drawdown % before stopping")
    daily_loss_limit: float = Field(default=1000.0, description="Max daily loss in currency")
    max_open_positions: int = Field(default=5, description="Max number of open positions")
    max_leverage: float = Field(default=1.0, description="Max leverage allowed")
    stop_loss_pct: float = Field(default=2.0, description="Default stop loss %")
    take_profit_pct: float = Field(default=5.0, description="Default take profit %")


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: str = Field(default="INFO", description="Logging level")
    file_path: str = Field(default="logs/trading.log", description="Log file path")
    rotation: str = Field(default="00:00", description="Log rotation time")
    retention: str = Field(default="30 days", description="Log retention period")
    format: str = Field(
        default="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name} | {message}",
        description="Log format",
    )
    debug_diagnostics: bool = Field(
        default=False,
        description="Enable deep traceback diagnostics in local debugging sessions",
    )


class TradingConfig(BaseSettings):
    """Main trading system configuration."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Trading mode
    mode: str = Field(default="paper", description="Trading mode: paper, backtest, live")
    initial_capital: float = Field(default=100000.0, description="Initial capital")

    # Exchange settings
    default_exchange: str = Field(default="binance", description="Default exchange")
    symbols: list[str] = Field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT"], description="Default trading symbols"
    )

    # Data settings
    data_cache_dir: str = Field(default="data/cache", description="Data cache directory")

    # Rate limiting
    rate_limit_requests_per_second: float = Field(default=10.0, description="API rate limit")

    # Sub-configurations
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    kraken: KrakenConfig = Field(default_factory=KrakenConfig)
    coinbase: CoinbaseConfig = Field(default_factory=CoinbaseConfig)
    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @validator("mode")
    def validate_mode(cls, v: str) -> str:
        """Validate trading mode."""
        allowed = ["paper", "backtest", "live"]
        if v not in allowed:
            raise ValueError(f"mode must be one of {allowed}")
        return v


# Global config instance
_config: Optional[TradingConfig] = None


def get_config() -> TradingConfig:
    """Get or create global config instance."""
    global _config
    if _config is None:
        _config = TradingConfig()
    return _config


def reload_config() -> TradingConfig:
    """Reload configuration from environment."""
    global _config
    _config = TradingConfig()
    return _config


def set_config(config: TradingConfig) -> None:
    """Set global config instance (useful for testing)."""
    global _config
    _config = config


def resolve_database_url(env: Mapping[str, str] | None = None) -> str:
    """Resolve the database URL from env override or validated config."""
    env_map = env or os.environ
    explicit_url = env_map.get("DATABASE_URL")
    if explicit_url:
        return explicit_url
    return TradingConfig().database.url
