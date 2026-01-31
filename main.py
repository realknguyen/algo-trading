#!/usr/bin/env python3
"""Main entry point for the algorithmic trading system."""

import asyncio
import argparse
import sys
from decimal import Decimal
from typing import Optional

import pandas as pd

from config.settings import get_config, TradingConfig
from logging.log_config import setup_logging, TradingLogger
from database.models import init_db, create_engine_from_config

# Import adapters
from adapters import BinanceAdapter, KrakenAdapter, CoinbaseAdapter

# Import order management
from order_management import OrderManager

# Import risk management
from risk_management import RiskManager, RiskLimits

# Import algorithms
from algorithms import BaseAlgorithm, AlgorithmConfig
from algorithms.sma_crossover import SmaCrossoverStrategy

# Import backtesting
from backtesting import BacktestEngine

# Import data
from src.data.fetcher import DataFetcher


logger = TradingLogger("Main")


def create_exchange_adapter(config: TradingConfig):
    """Create exchange adapter based on configuration."""
    exchange_name = config.default_exchange.lower()
    
    if exchange_name == "binance":
        return BinanceAdapter(
            api_key=config.binance.api_key,
            api_secret=config.binance.api_secret,
            sandbox=config.binance.testnet
        )
    elif exchange_name == "kraken":
        return KrakenAdapter(
            api_key=config.kraken.api_key,
            api_secret=config.kraken.api_secret,
            sandbox=config.kraken.sandbox
        )
    elif exchange_name == "coinbase":
        return CoinbaseAdapter(
            api_key=config.coinbase.api_key,
            api_secret=config.coinbase.api_secret,
            passphrase=config.coinbase.passphrase,
            sandbox=config.coinbase.sandbox
        )
    else:
        raise ValueError(f"Unsupported exchange: {exchange_name}")


async def run_backtest(args):
    """Run backtesting mode."""
    logger.logger.info("Starting backtest mode")
    
    config = get_config()
    
    # Fetch data
    fetcher = DataFetcher(cache_dir=config.data_cache_dir)
    
    data = {}
    for symbol in args.symbols:
        try:
            df = fetcher.fetch(
                symbol,
                start=args.start,
                end=args.end,
                interval=args.interval
            )
            data[symbol] = df
            logger.logger.info(f"Fetched {len(df)} rows for {symbol}")
        except Exception as e:
            logger.logger.error("fetch", f"Failed to fetch {symbol}: {e}")
    
    if not data:
        logger.logger.error("backtest", "No data available for backtest")
        return
    
    # Create algorithm
    if args.strategy == "sma_crossover":
        algorithm = SmaCrossoverStrategy(
            symbols=args.symbols,
            fast_period=args.fast_period or 20,
            slow_period=args.slow_period or 50
        )
    else:
        logger.logger.error("backtest", f"Unknown strategy: {args.strategy}")
        return
    
    # Run backtest
    engine = BacktestEngine(
        initial_capital=args.capital or 100000.0,
        commission=args.commission or 0.001
    )
    
    result = await engine.run(algorithm, data)
    
    # Print results
    print(result.summary())
    
    # Save results if requested
    if args.output:
        import json
        with open(args.output, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        logger.logger.info(f"Results saved to {args.output}")


async def run_paper_trading(args):
    """Run paper trading mode."""
    logger.logger.info("Starting paper trading mode")
    
    config = get_config()
    
    # Create exchange adapter
    adapter = create_exchange_adapter(config)
    
    # Connect to exchange
    if not await adapter.connect():
        logger.logger.error("paper", "Failed to connect to exchange")
        return
    
    try:
        # Create order manager
        order_manager = OrderManager(adapter)
        await order_manager.start()
        
        # Create risk manager
        risk_limits = RiskLimits(
            max_position_size_pct=config.risk.max_position_size,
            max_drawdown_pct=config.risk.max_drawdown_pct,
            daily_loss_limit=config.risk.daily_loss_limit,
            max_open_positions=config.risk.max_open_positions
        )
        risk_manager = RiskManager(
            limits=risk_limits,
            initial_capital=Decimal(str(config.initial_capital))
        )
        
        # Create algorithm
        if args.strategy == "sma_crossover":
            algorithm = SmaCrossoverStrategy(
                symbols=args.symbols,
                fast_period=args.fast_period or 20,
                slow_period=args.slow_period or 50,
                order_manager=order_manager,
                risk_manager=risk_manager
            )
        else:
            logger.logger.error("paper", f"Unknown strategy: {args.strategy}")
            return
        
        # Initialize with historical data
        fetcher = DataFetcher(cache_dir=config.data_cache_dir)
        data = {}
        for symbol in args.symbols:
            try:
                df = fetcher.fetch(symbol, period="30d", interval=args.interval)
                data[symbol] = df
            except Exception as e:
                logger.logger.error("fetch", f"Failed to fetch {symbol}: {e}")
        
        algorithm.initialize(data)
        await algorithm.start()
        
        logger.logger.info(f"Paper trading started with {args.strategy} strategy")
        logger.logger.info(f"Monitoring symbols: {args.symbols}")
        logger.logger.info("Press Ctrl+C to stop")
        
        # Main loop - simplified for demo
        try:
            while True:
                # In real implementation, this would:
                # 1. Fetch latest data via WebSocket
                # 2. Call algorithm.process_data()
                # 3. Handle signals and orders
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.logger.info("Stopping paper trading...")
        
        await algorithm.stop()
        await order_manager.stop()
        
    finally:
        await adapter.disconnect()


async def run_live_trading(args):
    """Run live trading mode."""
    logger.logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
    
    # Safety check
    print("\n" + "="*60)
    print("WARNING: You are about to start LIVE TRADING")
    print("REAL MONEY WILL BE AT RISK!")
    print("="*60 + "\n")
    
    confirm = input("Type 'LIVE' to confirm: ")
    if confirm != "LIVE":
        logger.logger.info("Live trading cancelled")
        return
    
    # Similar to paper trading but with additional safeguards
    logger.logger.info("Live trading not yet implemented")


async def init_database(args):
    """Initialize database tables."""
    logger.logger.info("Initializing database")
    
    config = get_config()
    
    try:
        engine = create_engine_from_config(config)
        init_db(engine)
        logger.logger.info("Database initialized successfully")
    except Exception as e:
        logger.logger.error("database", f"Failed to initialize database: {e}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Algorithmic Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run backtest
  python main.py backtest --strategy sma_crossover --symbols AAPL MSFT --start 2023-01-01 --end 2024-01-01

  # Run paper trading
  python main.py paper --strategy sma_crossover --symbols BTCUSDT

  # Initialize database
  python main.py init-db
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Backtest command
    backtest_parser = subparsers.add_parser('backtest', help='Run backtest')
    backtest_parser.add_argument('--strategy', required=True, help='Strategy name')
    backtest_parser.add_argument('--symbols', nargs='+', required=True, help='Symbols to trade')
    backtest_parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    backtest_parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    backtest_parser.add_argument('--interval', default='1d', help='Data interval')
    backtest_parser.add_argument('--capital', type=float, help='Initial capital')
    backtest_parser.add_argument('--commission', type=float, help='Commission rate')
    backtest_parser.add_argument('--fast-period', type=int, help='Fast SMA period')
    backtest_parser.add_argument('--slow-period', type=int, help='Slow SMA period')
    backtest_parser.add_argument('--output', help='Output file for results')
    
    # Paper trading command
    paper_parser = subparsers.add_parser('paper', help='Run paper trading')
    paper_parser.add_argument('--strategy', required=True, help='Strategy name')
    paper_parser.add_argument('--symbols', nargs='+', required=True, help='Symbols to trade')
    paper_parser.add_argument('--interval', default='1h', help='Data interval')
    paper_parser.add_argument('--fast-period', type=int, help='Fast SMA period')
    paper_parser.add_argument('--slow-period', type=int, help='Slow SMA period')
    
    # Live trading command
    live_parser = subparsers.add_parser('live', help='Run live trading (USE WITH CAUTION)')
    live_parser.add_argument('--strategy', required=True, help='Strategy name')
    live_parser.add_argument('--symbols', nargs='+', required=True, help='Symbols to trade')
    
    # Init database command
    subparsers.add_parser('init-db', help='Initialize database')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Setup logging
    setup_logging()
    
    # Run command
    try:
        if args.command == 'backtest':
            asyncio.run(run_backtest(args))
        elif args.command == 'paper':
            asyncio.run(run_paper_trading(args))
        elif args.command == 'live':
            asyncio.run(run_live_trading(args))
        elif args.command == 'init-db':
            asyncio.run(init_database(args))
    except KeyboardInterrupt:
        logger.logger.info("Interrupted by user")
    except Exception as e:
        logger.logger.exception("Unhandled error")
        raise


if __name__ == "__main__":
    main()
