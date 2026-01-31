"""Tests for broker base interface."""

import pytest
from abc import ABC
from dataclasses import dataclass
from unittest.mock import Mock

from src.broker import BaseBroker, Order, Position, OrderType, OrderSide


class TestOrder:
    """Test cases for Order dataclass."""
    
    def test_order_creation(self):
        """Test creating an order."""
        order = Order(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET
        )
        
        assert order.symbol == "AAPL"
        assert order.side == OrderSide.BUY
        assert order.quantity == 100
        assert order.order_type == OrderType.MARKET
        assert order.limit_price is None
        assert order.stop_price is None
        assert order.time_in_force == "day"
    
    def test_limit_order_creation(self):
        """Test creating a limit order."""
        order = Order(
            symbol="MSFT",
            side=OrderSide.SELL,
            quantity=50,
            order_type=OrderType.LIMIT,
            limit_price=300.0,
            time_in_force="gtc"
        )
        
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == 300.0
        assert order.time_in_force == "gtc"


class TestPosition:
    """Test cases for Position dataclass."""
    
    def test_position_creation(self):
        """Test creating a position."""
        position = Position(
            symbol="AAPL",
            quantity=100,
            avg_entry_price=150.0,
            current_price=155.0,
            unrealized_pl=500.0
        )
        
        assert position.symbol == "AAPL"
        assert position.quantity == 100
        assert position.avg_entry_price == 150.0
        assert position.current_price == 155.0
        assert position.unrealized_pl == 500.0


class TestBaseBroker:
    """Test cases for BaseBroker abstract class."""
    
    def test_base_broker_is_abstract(self):
        """Test that BaseBroker cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseBroker(api_key="test", api_secret="test")
    
    def test_concrete_broker_can_be_created(self):
        """Test that a concrete broker implementation can be created."""
        
        class ConcreteBroker(BaseBroker):
            def connect(self):
                self._connected = True
            
            def disconnect(self):
                self._connected = False
            
            def get_account(self):
                return {'cash': 100000}
            
            def get_positions(self):
                return []
            
            def submit_order(self, order):
                return {'id': '123', 'status': 'filled'}
            
            def cancel_order(self, order_id):
                return True
            
            def get_order(self, order_id):
                return {'id': order_id, 'status': 'filled'}
        
        broker = ConcreteBroker(api_key="test", api_secret="test", paper=True)
        
        assert broker.api_key == "test"
        assert broker.api_secret == "test"
        assert broker.paper is True
        assert broker._connected is False
        
        broker.connect()
        assert broker._connected is True
        
        broker.disconnect()
        assert broker._connected is False


class TestOrderType:
    """Test cases for OrderType enum."""
    
    def test_order_type_values(self):
        """Test order type enum values."""
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"


class TestOrderSide:
    """Test cases for OrderSide enum."""
    
    def test_order_side_values(self):
        """Test order side enum values."""
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"
