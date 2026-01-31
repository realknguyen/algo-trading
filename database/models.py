"""Database models for the trading system."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from enum import Enum as PyEnum

from sqlalchemy import (
    create_engine, Column, String, Float, DateTime, 
    Integer, Boolean, ForeignKey, Text, Numeric, Enum,
    Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

Base = declarative_base()


class OrderStatus(str, PyEnum):
    """Order status enumeration."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderType(str, PyEnum):
    """Order type enumeration."""
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"


class OrderSide(str, PyEnum):
    """Order side enumeration."""
    BUY = "buy"
    SELL = "sell"


class TradeSide(str, PyEnum):
    """Trade side enumeration."""
    LONG = "long"
    SHORT = "short"


class Algorithm(Base):
    """Trading algorithm model."""
    __tablename__ = "algorithms"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    class_name = Column(String(255), nullable=False)
    module_path = Column(String(500), nullable=False)
    parameters = Column(Text, nullable=True)  # JSON string
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    orders = relationship("Order", back_populates="algorithm")
    trades = relationship("Trade", back_populates="algorithm")
    
    __table_args__ = (
        Index("idx_algorithm_name", "name"),
        Index("idx_algorithm_active", "is_active"),
    )


class Order(Base):
    """Order model."""
    __tablename__ = "orders"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_id = Column(String(100), nullable=True, index=True)  # Exchange order ID
    
    # Order details
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(Enum(OrderSide), nullable=False)
    order_type = Column(Enum(OrderType), nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    
    # Quantities
    quantity = Column(Numeric(20, 8), nullable=False)
    filled_quantity = Column(Numeric(20, 8), default=0)
    remaining_quantity = Column(Numeric(20, 8), nullable=True)
    
    # Prices
    price = Column(Numeric(20, 8), nullable=True)  # Limit price
    stop_price = Column(Numeric(20, 8), nullable=True)  # Stop price
    avg_fill_price = Column(Numeric(20, 8), nullable=True)
    
    # Metadata
    algorithm_id = Column(String(36), ForeignKey("algorithms.id"), nullable=True)
    exchange = Column(String(50), nullable=False)
    time_in_force = Column(String(20), default="GTC")  # GTC, IOC, FOK, DAY
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    submitted_at = Column(DateTime, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    
    # Additional data
    commission = Column(Numeric(20, 8), default=0)
    commission_asset = Column(String(10), nullable=True)
    client_order_id = Column(String(100), nullable=True)
    
    # Relationships
    algorithm = relationship("Algorithm", back_populates="orders")
    trades = relationship("Trade", back_populates="order")
    
    __table_args__ = (
        Index("idx_order_symbol", "symbol"),
        Index("idx_order_status", "status"),
        Index("idx_order_external_id", "external_id"),
        Index("idx_order_created", "created_at"),
    )


class Trade(Base):
    """Trade (filled order) model."""
    __tablename__ = "trades"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_id = Column(String(100), nullable=True, index=True)  # Exchange trade ID
    
    # Relationships
    order_id = Column(String(36), ForeignKey("orders.id"), nullable=False)
    algorithm_id = Column(String(36), ForeignKey("algorithms.id"), nullable=True)
    
    # Trade details
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(Enum(OrderSide), nullable=False)
    trade_side = Column(Enum(TradeSide), default=TradeSide.LONG)
    
    # Quantities and prices
    quantity = Column(Numeric(20, 8), nullable=False)
    price = Column(Numeric(20, 8), nullable=False)
    
    # P&L
    realized_pnl = Column(Numeric(20, 8), nullable=True)
    unrealized_pnl = Column(Numeric(20, 8), nullable=True)
    
    # Costs
    commission = Column(Numeric(20, 8), default=0)
    commission_asset = Column(String(10), nullable=True)
    
    # Timestamps
    entry_time = Column(DateTime, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Exchange info
    exchange = Column(String(50), nullable=False)
    
    # Relationships
    order = relationship("Order", back_populates="trades")
    algorithm = relationship("Algorithm", back_populates="trades")
    
    __table_args__ = (
        Index("idx_trade_symbol", "symbol"),
        Index("idx_trade_created", "created_at"),
    )


class Position(Base):
    """Current position model."""
    __tablename__ = "positions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    
    # Position details
    quantity = Column(Numeric(20, 8), nullable=False, default=0)
    avg_entry_price = Column(Numeric(20, 8), nullable=True)
    current_price = Column(Numeric(20, 8), nullable=True)
    
    # P&L
    unrealized_pnl = Column(Numeric(20, 8), default=0)
    realized_pnl = Column(Numeric(20, 8), default=0)
    
    # Metadata
    exchange = Column(String(50), nullable=False)
    
    # Timestamps
    opened_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_position_symbol", "symbol"),
    )


class Transaction(Base):
    """Transaction log model."""
    __tablename__ = "transactions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Transaction details
    type = Column(String(50), nullable=False)  # deposit, withdrawal, fee, trade, etc.
    asset = Column(String(20), nullable=False)
    amount = Column(Numeric(20, 8), nullable=False)
    
    # Pricing
    price = Column(Numeric(20, 8), nullable=True)
    value = Column(Numeric(20, 8), nullable=True)
    
    # Metadata
    exchange = Column(String(50), nullable=True)
    external_id = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    
    # Timestamps
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_transaction_type", "type"),
        Index("idx_transaction_asset", "asset"),
        Index("idx_transaction_timestamp", "timestamp"),
    )


class SystemLog(Base):
    """System log model."""
    __tablename__ = "system_logs"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Log details
    level = Column(String(20), nullable=False)  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    source = Column(String(100), nullable=False)  # Module/component name
    message = Column(Text, nullable=False)
    
    # Structured data
    event_type = Column(String(50), nullable=True)  # trade, signal, risk, order_status, etc.
    metadata_json = Column(Text, nullable=True)  # JSON string for structured data
    
    # Timestamps
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_log_level", "level"),
        Index("idx_log_source", "source"),
        Index("idx_log_event_type", "event_type"),
        Index("idx_log_timestamp", "timestamp"),
    )


class BacktestResult(Base):
    """Backtest result model."""
    __tablename__ = "backtest_results"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Algorithm info
    algorithm_id = Column(String(36), ForeignKey("algorithms.id"), nullable=True)
    algorithm_name = Column(String(255), nullable=False)
    parameters = Column(Text, nullable=True)  # JSON string
    
    # Test period
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    symbols = Column(Text, nullable=False)  # JSON array
    
    # Results
    initial_capital = Column(Numeric(20, 8), nullable=False)
    final_capital = Column(Numeric(20, 8), nullable=False)
    total_return_pct = Column(Numeric(10, 4), nullable=False)
    
    # Metrics
    sharpe_ratio = Column(Numeric(10, 4), nullable=True)
    max_drawdown_pct = Column(Numeric(10, 4), nullable=True)
    win_rate = Column(Numeric(10, 4), nullable=True)
    profit_factor = Column(Numeric(10, 4), nullable=True)
    num_trades = Column(Integer, default=0)
    
    # Additional data
    equity_curve_json = Column(Text, nullable=True)  # JSON array
    trades_json = Column(Text, nullable=True)  # JSON array
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_backtest_algorithm", "algorithm_name"),
        Index("idx_backtest_created", "created_at"),
    )


# Database engine factory
def create_engine_from_config(config):
    """Create SQLAlchemy engine from config."""
    return create_engine(config.database.url)


def create_async_engine_from_config(config):
    """Create async SQLAlchemy engine from config."""
    return create_async_engine(config.database.async_url)


def init_db(engine):
    """Initialize database tables."""
    Base.metadata.create_all(engine)


SessionLocal = None


def get_session_factory(engine):
    """Get session factory bound to engine."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


async def get_async_session(async_engine) -> AsyncSession:
    """Get async database session."""
    async with AsyncSession(async_engine) as session:
        yield session
