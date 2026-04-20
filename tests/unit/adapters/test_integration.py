import pytest
import asyncio
import time
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch
from src.adapters.normalizer import (
    normalize_binance_trade,
    normalize_binance_orderbook,
    normalize_coinbase_trade,
    normalize_kraken_ticker,
    ExchangeName,
    TradeSide,
)
from src.adapters.health_monitor import ExchangeHealthMonitor, ConnectionStatus, CircuitBreakerOpen
from src.adapters.testnet import TestnetOrderValidator, BINANCE_TESTNET, TestnetValidationError

# --- Normalizer Tests ---


def test_binance_trade_normalization():
    raw_trade = {"T": 1600000000000, "p": "50000.00", "q": "0.1", "m": False, "t": "12345"}
    normalized = normalize_binance_trade(raw_trade, "BTCUSDT")
    assert normalized.price == Decimal("50000.00")
    assert normalized.quantity == Decimal("0.1")
    assert normalized.side == TradeSide.BUY
    assert normalized.exchange == ExchangeName.BINANCE


def test_kraken_ticker_normalization():
    raw_ticker = {
        "XXBTZUSD": {
            "a": ["50001.0", "1", "1.0"],
            "b": ["50000.0", "1", "1.0"],
            "c": ["50000.5", "0.1"],
            "v": ["100", "200"],
            "o": "49000.0",
        }
    }
    normalized = normalize_kraken_ticker(raw_ticker, "BTCUSD")
    assert normalized.bid == Decimal("50000.0")
    assert normalized.ask == Decimal("50001.0")
    assert normalized.last == Decimal("50000.5")
    assert normalized.exchange == ExchangeName.KRAKEN


# --- Health Monitor Tests ---


@pytest.mark.asyncio
async def test_health_monitor_connection_flow():
    monitor = ExchangeHealthMonitor()
    mock_connect = AsyncMock(return_value="connected_obj")

    monitor.register_exchange("binance", mock_connect)
    success = await monitor.connect("binance")

    assert success is True
    assert monitor.is_connected("binance") is True
    assert monitor.metrics["binance"].status == ConnectionStatus.CONNECTED


@pytest.mark.asyncio
async def test_circuit_breaker_logic():
    monitor = ExchangeHealthMonitor(enable_circuit_breaker=True)
    # Mock a failing connection
    mock_connect = AsyncMock(side_effect=Exception("API Down"))

    monitor.register_exchange("binance", mock_connect)

    # Trigger failures to open circuit (threshold is 5)
    for _ in range(6):
        await monitor.connect("binance")

    assert monitor.circuits["binance"].is_open is True
    assert monitor.metrics["binance"].status == ConnectionStatus.CIRCUIT_OPEN


# --- Testnet Tests ---


def test_testnet_validator_safety():
    validator = TestnetOrderValidator(BINANCE_TESTNET)

    # Valid order
    result = validator.validate_order("BTCUSDT", "buy", Decimal("0.1"), Decimal("40000"))
    assert result["valid"] is True

    # Exceed max value (Max for Binance Testnet in config is 10000)
    with pytest.raises(TestnetValidationError):
        validator.validate_order("BTCUSDT", "buy", Decimal("10"), Decimal("50000"))


def test_testnet_blocked_symbols():
    from src.adapters.testnet import KRAKEN_PRODUCTION

    validator = TestnetOrderValidator(KRAKEN_PRODUCTION)

    with pytest.raises(TestnetValidationError, match="is blocked"):
        validator.validate_order("BTCUSD", "buy", Decimal("0.1"))
