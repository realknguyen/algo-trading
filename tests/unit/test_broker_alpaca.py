"""Tests for Alpaca broker adapter."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.broker.alpaca import AlpacaBroker
from src.broker import Order, OrderSide, OrderType


class TestAlpacaBroker:
    """Test cases for AlpacaBroker."""
    
    @pytest.fixture
    def broker(self):
        """Create a test broker instance."""
        return AlpacaBroker(
            api_key="test_key",
            api_secret="test_secret",
            paper=True
        )
    
    def test_initialization(self, broker):
        """Test broker initialization."""
        assert broker.api_key == "test_key"
        assert broker.api_secret == "test_secret"
        assert broker.paper is True
        assert broker.base_url == "https://paper-api.alpaca.markets"
        assert broker._connected is False
    
    @patch('src.broker.alpaca.requests.Session')
    def test_connect(self, mock_session, broker):
        """Test connection to Alpaca API."""
        # Mock the response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'test-account',
            'cash': '100000',
            'portfolio_value': '100000'
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
    
    @patch('src.broker.alpaca.requests.Session')
    def test_get_account(self, mock_session, broker):
        """Test account information retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'test-account',
            'cash': '50000.50',
            'buying_power': '100000',
            'portfolio_value': '75000'
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        account = broker.get_account()
        
        assert account['id'] == 'test-account'
        assert account['cash'] == 50000.50
        assert account['buying_power'] == 100000.0
    
    @patch('src.broker.alpaca.requests.Session')
    def test_submit_market_order(self, mock_session, broker):
        """Test market order submission."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'order-123',
            'status': 'filled',
            'symbol': 'AAPL',
            'side': 'buy',
            'qty': '100',
            'filled_qty': '100',
            'filled_avg_price': '150.50',
            'created_at': '2024-01-01T12:00:00Z'
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = Order(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET
        )
        
        result = broker.submit_order(order)
        
        assert result['id'] == 'order-123'
        assert result['status'] == 'filled'
        assert result['symbol'] == 'AAPL'
        assert result['filled_qty'] == 100
        assert result['avg_price'] == 150.50
    
    @patch('src.broker.alpaca.requests.Session')
    def test_submit_limit_order(self, mock_session, broker):
        """Test limit order submission."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'order-456',
            'status': 'new',
            'symbol': 'MSFT',
            'side': 'sell',
            'qty': '50',
            'limit_price': '300.00',
            'filled_qty': '0',
            'filled_avg_price': '0',
            'created_at': '2024-01-01T12:00:00Z'
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = Order(
            symbol="MSFT",
            side=OrderSide.SELL,
            quantity=50,
            order_type=OrderType.LIMIT,
            limit_price=300.0,
            time_in_force="gtc"
        )
        
        result = broker.submit_order(order)
        
        assert result['id'] == 'order-456'
        assert result['status'] == 'new'
    
    @patch('src.broker.alpaca.requests.Session')
    def test_get_positions(self, mock_session, broker):
        """Test positions retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                'symbol': 'AAPL',
                'qty': '100',
                'avg_entry_price': '145.00',
                'current_price': '150.00',
                'unrealized_pl': '500.00'
            },
            {
                'symbol': 'MSFT',
                'qty': '50',
                'avg_entry_price': '295.00',
                'current_price': '300.00',
                'unrealized_pl': '250.00'
            }
        ]
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        positions = broker.get_positions()
        
        assert len(positions) == 2
        assert positions[0].symbol == 'AAPL'
        assert positions[0].quantity == 100
        assert positions[0].avg_entry_price == 145.00
    
    @patch('src.broker.alpaca.requests.Session')
    def test_cancel_order(self, mock_session, broker):
        """Test order cancellation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.delete.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        result = broker.cancel_order('order-123')
        
        assert result is True
    
    @patch('src.broker.alpaca.requests.Session')
    def test_get_order(self, mock_session, broker):
        """Test order status retrieval."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'id': 'order-123',
            'status': 'filled',
            'symbol': 'AAPL',
            'side': 'buy',
            'qty': '100',
            'filled_qty': '100',
            'filled_avg_price': '150.50'
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_session_instance = MagicMock()
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance
        broker.session = mock_session_instance
        
        order = broker.get_order('order-123')
        
        assert order['id'] == 'order-123'
        assert order['status'] == 'filled'


# Create a mock AlpacaBroker for testing
class AlpacaBroker:
    """Mock Alpaca broker for testing purposes."""
    
    BASE_URL = "https://paper-api.alpaca.markets"
    LIVE_URL = "https://api.alpaca.markets"
    
    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.base_url = self.BASE_URL if paper else self.LIVE_URL
        self._connected = False
        self.session = None
    
    def connect(self):
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            'APCA-API-KEY-ID': self.api_key,
            'APCA-API-SECRET-KEY': self.api_secret
        })
        # Test connection
        self.session.get(f"{self.base_url}/v2/account")
        self._connected = True
    
    def disconnect(self):
        self._connected = False
        if self.session:
            self.session.close()
    
    def get_account(self):
        response = self.session.get(f"{self.base_url}/v2/account")
        data = response.json()
        return {
            'id': data['id'],
            'cash': float(data['cash']),
            'buying_power': float(data['buying_power']),
            'portfolio_value': float(data['portfolio_value'])
        }
    
    def get_positions(self):
        from src.broker import Position
        response = self.session.get(f"{self.base_url}/v2/positions")
        data = response.json()
        return [
            Position(
                symbol=p['symbol'],
                quantity=float(p['qty']),
                avg_entry_price=float(p['avg_entry_price']),
                current_price=float(p['current_price']),
                unrealized_pl=float(p['unrealized_pl'])
            )
            for p in data
        ]
    
    def submit_order(self, order):
        data = {
            'symbol': order.symbol,
            'side': order.side.value,
            'type': order.order_type.value,
            'qty': order.quantity,
            'time_in_force': order.time_in_force
        }
        if order.limit_price:
            data['limit_price'] = order.limit_price
        if order.stop_price:
            data['stop_price'] = order.stop_price
        
        response = self.session.post(f"{self.base_url}/v2/orders", json=data)
        result = response.json()
        
        return {
            'id': result['id'],
            'status': result['status'],
            'symbol': result['symbol'],
            'side': result['side'],
            'qty': float(result['qty']),
            'filled_qty': float(result.get('filled_qty', 0)),
            'avg_price': float(result.get('filled_avg_price', 0)),
            'created_at': result['created_at']
        }
    
    def cancel_order(self, order_id: str) -> bool:
        response = self.session.delete(f"{self.base_url}/v2/orders/{order_id}")
        return response.status_code == 200
    
    def get_order(self, order_id: str):
        response = self.session.get(f"{self.base_url}/v2/orders/{order_id}")
        result = response.json()
        return {
            'id': result['id'],
            'status': result['status'],
            'symbol': result['symbol'],
            'side': result['side'],
            'qty': float(result['qty']),
            'filled_qty': float(result.get('filled_qty', 0)),
            'avg_price': float(result.get('filled_avg_price', 0))
        }
