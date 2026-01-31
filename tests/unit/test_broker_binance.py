"""Tests for Binance broker adapter."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.broker.binance import BinanceBroker
from src.broker import Order, OrderSide, OrderType


class TestBinanceBroker:
    """Test cases for BinanceBroker."""
    
    @pytest.fixture
    def broker(self):
        """Create a test broker instance."""
        return BinanceBroker(
            api_key="test_key",
            api_secret="test_secret",
            testnet=True
        )
    
    def test_initialization(self, broker):
        """Test broker initialization."""
        assert broker.api_key == "test_key"
        assert broker.api_secret == "test_secret"
        assert broker.testnet is True
        assert broker.paper is True  # Aliased
        assert broker.base_url == "https://testnet.binance.vision"
        assert broker._connected is False
    
    @patch('src.broker.binance.requests.Session')
    def test_connect(self, mock_session, broker):
        """Test connection to Binance API."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'accountId': 12345,
            'balances': [{'asset': 'USDT', 'free': '1000', 'locked': '0'}]
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        broker.connect()
        
        assert broker._connected is True
    
    def test_disconnect(self, broker):
        """Test disconnection."""
        broker._connected = True
        broker.disconnect()
        assert broker._connected is False
    
    @patch('src.broker.binance.requests.Session')
    def test_get_account(self, mock_session, broker):
        """Test account information retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'accountId': 12345,
            'balances': [
                {'asset': 'USDT', 'free': '5000.50', 'locked': '100.0'},
                {'asset': 'BTC', 'free': '0.5', 'locked': '0.0'}
            ]
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        account = broker.get_account()
        
        assert account['id'] == '12345'
        assert account['cash'] == 5000.50
        assert account['currency'] == 'USDT'
    
    @patch('src.broker.binance.requests.Session')
    def test_submit_market_order(self, mock_session, broker):
        """Test market order submission."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'orderId': 123456,
            'status': 'FILLED',
            'symbol': 'BTCUSDT',
            'side': 'BUY',
            'origQty': '0.001',
            'executedQty': '0.001',
            'avgPrice': '50000.0',
            'transactTime': 1234567890000
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = Order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.001,
            order_type=OrderType.MARKET
        )
        
        result = broker.submit_order(order)
        
        assert result['id'] == '123456'
        assert result['status'] == 'filled'
        assert result['symbol'] == 'BTCUSDT'
    
    @patch('src.broker.binance.requests.Session')
    def test_submit_limit_order(self, mock_session, broker):
        """Test limit order submission."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'orderId': 123457,
            'status': 'NEW',
            'symbol': 'ETHUSDT',
            'side': 'SELL',
            'origQty': '0.1',
            'executedQty': '0',
            'price': '3000.0',
            'transactTime': 1234567890000
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = Order(
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            quantity=0.1,
            order_type=OrderType.LIMIT,
            limit_price=3000.0,
            time_in_force="gtc"
        )
        
        result = broker.submit_order(order)
        
        assert result['id'] == '123457'
        assert result['status'] == 'new'
    
    @patch('src.broker.binance.requests.Session')
    def test_cancel_order_with_symbol(self, mock_session, broker):
        """Test order cancellation with symbol."""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.delete.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        result = broker.cancel_order_with_symbol('123456', 'BTCUSDT')
        
        assert result is True
    
    @patch('src.broker.binance.requests.Session')
    def test_get_order(self, mock_session, broker):
        """Test order status retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'orderId': 123456,
            'status': 'FILLED',
            'symbol': 'BTCUSDT',
            'side': 'BUY',
            'origQty': '0.001',
            'executedQty': '0.001',
            'avgPrice': '50000.0',
            'time': 1234567890000
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = broker.get_order('123456', 'BTCUSDT')
        
        assert order['id'] == '123456'
        assert order['status'] == 'filled'
    
    def test_get_order_requires_symbol(self, broker):
        """Test that get_order requires symbol parameter."""
        with pytest.raises(ValueError, match="symbol"):
            broker.get_order('123456')
    
    @patch('src.broker.binance.requests.Session')
    def test_get_positions(self, mock_session, broker):
        """Test positions retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'balances': [
                {'asset': 'USDT', 'free': '5000', 'locked': '0'},
                {'asset': 'BTC', 'free': '0.5', 'locked': '0'},
                {'asset': 'ETH', 'free': '5', 'locked': '0'}
            ]
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_ticker = MagicMock()
        mock_ticker.json.return_value = {'price': '50000.0'}
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.side_effect = [mock_response, mock_ticker]
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        positions = broker.get_positions()
        
        # Should return BTC and ETH positions, not USDT
        assert len(positions) == 2
        assert positions[0].symbol == 'BTC'
        assert positions[0].quantity == 0.5
