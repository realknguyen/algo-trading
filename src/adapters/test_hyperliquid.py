"""Tests for Hyperliquid adapter.

This module contains unit tests for the Hyperliquid exchange adapter.
"""

import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

# Import the adapter
try:
    from src.adapters.hyperliquid import HyperliquidAdapter, FundingRate, AssetContext
    from src.adapters.base_adapter import Order, OrderSide, OrderType
    from src.adapters.exceptions import AuthenticationError, InvalidSymbolError
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from adapters.hyperliquid import HyperliquidAdapter, FundingRate, AssetContext
    from adapters.base_adapter import Order, OrderSide, OrderType
    from adapters.exceptions import AuthenticationError, InvalidSymbolError


class TestHyperliquidAdapter:
    """Test suite for HyperliquidAdapter."""

    @pytest.fixture
    def adapter(self):
        """Create a test adapter instance."""
        return HyperliquidAdapter(
            api_key="0x1234567890123456789012345678901234567890",
            api_secret="0x" + "11" * 32,
            sandbox=True,
        )

    def test_initialization(self, adapter):
        """Test adapter initialization."""
        assert adapter.exchange_name == "hyperliquid"
        assert adapter.wallet_address == "0x1234567890123456789012345678901234567890"
        assert adapter._sandbox == True
        assert adapter.base_url == "https://api.hyperliquid-testnet.xyz"

    def test_float_to_wire(self):
        """Test float to wire format conversion."""
        result = HyperliquidAdapter._float_to_wire(123.456789)
        assert isinstance(result, str)
        assert float(result) == pytest.approx(123.456789, rel=1e-8)

    def test_float_to_int(self):
        """Test float to int conversion with decimals."""
        result = HyperliquidAdapter._float_to_int(123.456789, 8)
        assert result == 12345678900

    def test_get_timestamp_ms(self):
        """Test timestamp generation."""
        ts = HyperliquidAdapter._get_timestamp_ms()
        assert isinstance(ts, int)
        assert ts > 0

    def test_address_to_bytes(self, adapter):
        """Test address to bytes conversion."""
        result = adapter._address_to_bytes("0x1234")
        assert result == b"\x124"

    @pytest.mark.asyncio
    async def test_load_meta(self, adapter):
        """Test metadata loading."""
        mock_response = {
            "universe": [{"name": "BTC", "szDecimals": 8}, {"name": "ETH", "szDecimals": 8}]
        }

        with patch.object(adapter, "_post_info", return_value=mock_response):
            await adapter._load_meta()

            assert adapter._meta_loaded == True
            assert adapter._coin_to_asset["BTC"] == 0
            assert adapter._coin_to_asset["ETH"] == 1

    def test_name_to_asset(self, adapter):
        """Test coin name to asset ID conversion."""
        adapter._coin_to_asset = {"BTC": 0, "ETH": 1}

        assert adapter._name_to_asset("BTC") == 0
        assert adapter._name_to_asset("ETH") == 1

        with pytest.raises(InvalidSymbolError):
            adapter._name_to_asset("UNKNOWN")

    def test_get_sz_decimals(self, adapter):
        """Test getting size decimals for assets."""
        adapter._asset_to_sz_decimals = {0: 8, 1: 8}

        assert adapter._get_sz_decimals(0) == 8
        assert adapter._get_sz_decimals(1) == 8
        assert adapter._get_sz_decimals(999) == 8  # Default

    @pytest.mark.asyncio
    async def test_get_account(self, adapter):
        """Test get_account method."""
        mock_state = {"crossMarginSummary": {"accountValue": "10000.0"}}
        mock_role = {"role": "user"}

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_info", side_effect=[mock_state, mock_role]):
                account = await adapter.get_account()

                assert account.account_id == adapter.wallet_address
                assert account.account_type == "perpetual"
                assert "user" in account.permissions

    @pytest.mark.asyncio
    async def test_get_balances(self, adapter):
        """Test get_balances method."""
        mock_state = {
            "crossMarginSummary": {"accountValue": "10000.0", "totalMarginUsed": "2000.0"},
            "withdrawable": "8000.0",
        }

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_info", return_value=mock_state):
                balances = await adapter.get_balances()

                assert len(balances) == 1
                assert balances[0].asset == "USDC"
                assert balances[0].total == Decimal("10000.0")
                assert balances[0].locked == Decimal("2000.0")
                assert balances[0].free == Decimal("8000.0")

    def test_order_type_to_wire(self, adapter):
        """Test order type conversion to wire format."""
        market = adapter._order_type_to_wire(OrderType.MARKET)
        assert market == {"limit": {"tif": "Ioc"}}

        limit = adapter._order_type_to_wire(OrderType.LIMIT)
        assert limit == {"limit": {"tif": "Gtc"}}

    def test_build_order_wire(self, adapter):
        """Test building order wire format."""
        adapter._asset_to_sz_decimals = {0: 8}
        adapter._coin_to_asset = {"BTC": 0}

        order = Order(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.1"),
            price=Decimal("40000"),
        )

        wire = adapter._build_order_wire(order, 0)

        assert wire["a"] == 0
        assert wire["b"] == True
        assert wire["r"] == False
        assert "p" in wire
        assert "s" in wire

    @pytest.mark.asyncio
    async def test_get_positions(self, adapter):
        """Test get_positions method."""
        mock_state = {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "0.5",
                        "entryPx": "40000",
                        "markPx": "41000",
                        "unrealizedPnl": "500",
                        "realizedPnl": "100",
                        "leverage": {"value": "10", "type": "cross"},
                    }
                }
            ]
        }

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_info", return_value=mock_state):
                positions = await adapter.get_positions()

                assert len(positions) == 1
                assert positions[0].symbol == "BTC"
                assert positions[0].quantity == Decimal("0.5")
                assert positions[0].leverage == Decimal("10")
                assert positions[0].margin_mode == "cross"

    @pytest.mark.asyncio
    async def test_get_orderbook(self, adapter):
        """Test get_orderbook method."""
        mock_book = {
            "levels": [
                [{"px": "40000", "sz": "1.0"}, {"px": "39999", "sz": "2.0"}],
                [{"px": "40001", "sz": "1.5"}, {"px": "40002", "sz": "3.0"}],
            ],
            "time": 1234567890,
        }

        adapter._coin_to_asset = {"BTC": 0}

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_info", return_value=mock_book):
                book = await adapter.get_orderbook("BTC")

                assert book.symbol == "BTC"
                assert len(book.bids) == 2
                assert len(book.asks) == 2
                assert book.best_bid == Decimal("40000")
                assert book.best_ask == Decimal("40001")

    @pytest.mark.asyncio
    async def test_get_historical_candles(self, adapter):
        """Test get_historical_candles method."""
        mock_candles = [
            {"t": 1609459200000, "o": "40000", "h": "41000", "l": "39000", "c": "40500", "v": "100"}
        ]

        adapter._coin_to_asset = {"BTC": 0}

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_info", return_value=mock_candles):
                candles = await adapter.get_historical_candles("BTC", "1h")

                assert len(candles) == 1
                assert candles[0].open == Decimal("40000")
                assert candles[0].high == Decimal("41000")
                assert candles[0].low == Decimal("39000")
                assert candles[0].close == Decimal("40500")
                assert candles[0].volume == Decimal("100")

    def test_interval_to_ms(self, adapter):
        """Test interval conversion to milliseconds."""
        assert adapter._interval_to_ms("1m") == 60000
        assert adapter._interval_to_ms("5m") == 300000
        assert adapter._interval_to_ms("1h") == 3600000
        assert adapter._interval_to_ms("1d") == 86400000

    @pytest.mark.asyncio
    async def test_update_leverage(self, adapter):
        """Test update_leverage method."""
        adapter._coin_to_asset = {"BTC": 0}

        mock_result = {"status": "ok"}

        with patch.object(adapter, "_load_meta", return_value=None):
            with patch.object(adapter, "_post_exchange", return_value=mock_result):
                result = await adapter.update_leverage("BTC", 10, True)
                assert result == True

    @pytest.mark.asyncio
    async def test_get_liquidation_price(self, adapter):
        """Test get_liquidation_price method."""
        mock_state = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.5", "liquidationPx": "35000"}}
            ]
        }

        with patch.object(adapter, "_post_info", return_value=mock_state):
            liq_px = await adapter.get_liquidation_price("BTC")
            assert liq_px == Decimal("35000")

    @pytest.mark.asyncio
    async def test_get_funding_rate(self, adapter):
        """Test get_funding_rate method."""
        mock_meta = {"universe": [{"name": "BTC"}, {"name": "ETH"}]}
        mock_ctxs = [
            {"funding": "0.0001", "premium": "0.00005"},
            {"funding": "0.0002", "premium": "0.0001"},
        ]

        with patch.object(adapter, "_post_info", return_value=[mock_meta, mock_ctxs]):
            funding = await adapter.get_funding_rate("BTC")

            assert isinstance(funding, FundingRate)
            assert funding.coin == "BTC"
            assert funding.funding_rate == Decimal("0.0001")
            assert funding.premium == Decimal("0.00005")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
