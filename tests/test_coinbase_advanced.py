"""Tests for Coinbase Advanced Trade adapter."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

# Mock the JWT libraries before import
pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from adapters.coinbase import (
    CoinbaseAdvancedTradeAdapter,
    CoinbaseAdapter,
    Order,
    OrderSide,
    OrderType,
    TimeInForce,
)


@pytest.fixture
def adapter():
    """Create test adapter instance."""
    return CoinbaseAdvancedTradeAdapter(
        api_key="organizations/test-org/apiKeys/test-key",
        api_secret="""-----BEGIN EC PRIVATE KEY-----
MHQCAQEEIBase64EncodedKeyForTesting==
-----END EC PRIVATE KEY-----
""",
        sandbox=True,
        portfolio_id="test-portfolio-uuid"
    )


@pytest.fixture
def sample_order():
    """Create sample order for testing."""
    return Order(
        symbol="BTC-USD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.1"),
        price=Decimal("50000"),
        time_in_force=TimeInForce.GTC,
        client_order_id="test-order-123"
    )


class TestCoinbaseAdapter:
    """Test suite for Coinbase Advanced Trade adapter."""
    
    def test_initialization(self, adapter):
        """Test adapter initialization."""
        assert adapter.sandbox is True
        assert adapter.portfolio_id == "test-portfolio-uuid"
        assert "sandbox" in adapter.base_url
        assert adapter.api_key == "organizations/test-org/apiKeys/test-key"
    
    def test_symbol_conversion(self, adapter):
        """Test symbol to product ID conversion."""
        # Standard format to Coinbase format
        assert adapter._to_coinbase_product_id("BTCUSD") == "BTC-USD"
        assert adapter._to_coinbase_product_id("ETHUSDT") == "ETH-USDT"
        assert adapter._to_coinbase_product_id("BTC-USD") == "BTC-USD"  # Already correct
        
        # Reverse conversion
        assert adapter._from_coinbase_product_id("BTC-USD") == "BTCUSD"
    
    def test_order_config_building(self, adapter, sample_order):
        """Test order configuration building."""
        config = adapter._build_order_config(sample_order)
        
        assert config['product_id'] == "BTC-USD"
        assert config['side'] == "BUY"
        assert 'client_order_id' in config
        assert config['portfolio_id'] == "test-portfolio-uuid"
        assert 'order_configuration' in config
        
        # Check limit order GTC config
        limit_config = config['order_configuration'].get('limit_limit_gtc')
        assert limit_config is not None
        assert limit_config['base_size'] == "0.1"
        assert limit_config['limit_price'] == "50000"
    
    def test_market_order_config(self, adapter):
        """Test market order configuration."""
        order = Order(
            symbol="BTC-USD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.1")
        )
        
        config = adapter._build_order_config(order)
        market_config = config['order_configuration'].get('market_market_ioc')
        assert market_config is not None
        assert market_config['quote_size'] == "0.1"
    
    def test_stop_order_config(self, adapter):
        """Test stop order configuration."""
        order = Order(
            symbol="BTC-USD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LOSS,
            quantity=Decimal("0.1"),
            stop_price=Decimal("45000")
        )
        
        config = adapter._build_order_config(order)
        stop_config = config['order_configuration'].get('stop_limit_stop_limit_gtc')
        assert stop_config is not None
        assert stop_config['stop_price'] == "45000"
        assert stop_config['stop_direction'] == "STOP_DIRECTION_STOP_DOWN"
    
    def test_order_status_parsing(self, adapter):
        """Test order status parsing."""
        assert adapter._parse_order_status('PENDING') == OrderStatus.PENDING
        assert adapter._parse_order_status('OPEN') == OrderStatus.OPEN
        assert adapter._parse_order_status('FILLED') == OrderStatus.FILLED
        assert adapter._parse_order_status('CANCELLED') == OrderStatus.CANCELLED
        assert adapter._parse_order_status('EXPIRED') == OrderStatus.EXPIRED
        assert adapter._parse_order_status('FAILED') == OrderStatus.REJECTED
    
    @pytest.mark.asyncio
    async def test_get_account(self, adapter):
        """Test get_account method."""
        with patch.object(adapter, '_make_request', new_callable=AsyncMock) as mock:
            mock.return_value = {'accounts': []}
            result = await adapter.get_account()
            
            mock.assert_called_once()
            call_args = mock.call_args
            assert call_args[0][0] == "GET"
            assert '/accounts' in call_args[0][1]
            assert call_args[1]['signed'] is True
    
    @pytest.mark.asyncio
    async def test_place_order(self, adapter, sample_order):
        """Test place_order method."""
        with patch.object(adapter, '_make_request', new_callable=AsyncMock) as mock:
            mock.return_value = {
                'success': True,
                'order_id': 'test-order-uuid-123'
            }
            
            with patch.object(adapter, 'get_order', new_callable=AsyncMock) as mock_get:
                mock_get.return_value = sample_order
                result = await adapter.place_order(sample_order)
                
                assert result.order_id == 'test-order-uuid-123'
                mock.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cancel_order(self, adapter):
        """Test cancel_order method."""
        with patch.object(adapter, '_make_request', new_callable=AsyncMock) as mock:
            mock.return_value = {
                'results': [{'success': True}]
            }
            
            result = await adapter.cancel_order("BTC-USD", "order-uuid-123")
            assert result is True
            
            call_args = mock.call_args
            assert call_args[0][0] == "POST"
            assert '/batch_cancel' in call_args[0][1]
    
    @pytest.mark.asyncio
    async def test_get_orderbook(self, adapter):
        """Test get_orderbook method."""
        with patch.object(adapter, '_make_request', new_callable=AsyncMock) as mock:
            mock.return_value = {
                'pricebook': {
                    'bids': [{'price': '50000', 'size': '1.0'}],
                    'asks': [{'price': '50100', 'size': '0.5'}],
                    'sequence': 12345
                }
            }
            
            result = await adapter.get_orderbook("BTC-USD", limit=100)
            
            assert len(result['bids']) == 1
            assert len(result['asks']) == 1
            assert result['bids'][0][0] == Decimal('50000')
            assert result['asks'][0][0] == Decimal('50100')
            assert result['sequence'] == 12345


class TestWebSocketMethods:
    """Test WebSocket subscription methods."""
    
    @pytest.mark.asyncio
    async def test_ws_subscription_messages(self, adapter):
        """Test WebSocket subscription message formatting."""
        with patch.object(adapter, '_ws_send', new_callable=AsyncMock) as mock:
            await adapter.subscribe_to_ticker(["BTC-USD"])
            
            call_args = mock.call_args
            message = call_args[0][0]
            assert message['type'] == 'subscribe'
            assert message['channel'] == 'ticker'
            assert 'BTC-USD' in message['product_ids']
    
    @pytest.mark.asyncio
    async def test_subscribe_to_level2(self, adapter):
        """Test level2 subscription."""
        with patch.object(adapter, '_ws_send', new_callable=AsyncMock) as mock:
            await adapter.subscribe_to_level2(["ETH-USD", "BTC-USD"])
            
            call_args = mock.call_args
            message = call_args[0][0]
            assert message['channel'] == 'level2'
            assert 'ETH-USD' in message['product_ids']
            assert 'BTC-USD' in message['product_ids']
    
    @pytest.mark.asyncio
    async def test_subscribe_to_user(self, adapter):
        """Test user channel subscription."""
        with patch.object(adapter, '_ws_send', new_callable=AsyncMock) as mock:
            await adapter.subscribe_to_user(["BTC-USD"])
            
            call_args = mock.call_args
            message = call_args[0][0]
            assert message['channel'] == 'user'


class TestPagination:
    """Test pagination handling."""
    
    @pytest.mark.asyncio
    async def test_get_open_orders_pagination(self, adapter):
        """Test that pagination is handled correctly."""
        responses = [
            {
                'orders': [{'order_id': '1', 'status': 'OPEN'}],
                'cursor': 'next-cursor',
                'has_next': True
            },
            {
                'orders': [{'order_id': '2', 'status': 'OPEN'}],
                'has_next': False
            }
        ]
        
        with patch.object(adapter, '_make_request', new_callable=AsyncMock) as mock:
            mock.side_effect = responses
            
            with patch.object(adapter, '_parse_order_response') as mock_parse:
                mock_parse.return_value = Order(
                    symbol="BTC-USD",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("0.1"),
                    status=OrderStatus.OPEN
                )
                
                orders = await adapter.get_open_orders()
                
                # Should have made 2 calls for pagination
                assert mock.call_count == 2
                assert len(orders) == 2


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
