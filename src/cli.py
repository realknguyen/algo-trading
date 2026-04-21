"""Simple CLI for common trading workflows."""

import asyncio
import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config.settings import get_config
from log_config import setup_logging
from src.backtest.runner import BacktestRunner
from src.data.fetcher import DataFetcher
from src.runtime import build_runtime_from_config, initialize_database
from src.strategy import create_strategy, describe_strategies, list_strategies


def _parse_param_args(raw_params: Optional[list[str]]) -> Dict[str, object]:
    parsed: Dict[str, object] = {}
    if not raw_params:
        return parsed

    for item in raw_params:
        if "=" not in item:
            raise ValueError(f"Invalid parameter '{item}'. Expected format key=value")

        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()

        lowered = value.lower()
        if lowered in {"true", "false"}:
            parsed[key] = lowered == "true"
        else:
            try:
                if "." in value:
                    parsed[key] = float(value)
                else:
                    parsed[key] = int(value)
            except ValueError:
                parsed[key] = value

    return parsed


def _validate_backtest_args(args: argparse.Namespace) -> None:
    """Validate backtest CLI arguments before execution."""
    if (args.start and not args.end) or (args.end and not args.start):
        raise ValueError("Provide both --start and --end together, or use --period")

    if args.start and args.end:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        if start > end:
            raise ValueError("start date must be on or before end date")

    if args.capital <= 0:
        raise ValueError("capital must be positive")

    if args.commission < 0:
        raise ValueError("commission must be non-negative")


def _validate_runtime_args(args: argparse.Namespace) -> list[str]:
    """Validate paper/live runtime arguments before execution."""
    symbols = list(args.symbols or [])
    if not symbols:
        config = get_config()
        symbols = list(config.symbols)

    if not symbols:
        raise ValueError("At least one symbol is required")

    if args.iterations <= 0:
        raise ValueError("iterations must be positive")

    if args.poll_seconds < 0:
        raise ValueError("poll-seconds must be non-negative")

    if args.lookback < 2:
        raise ValueError("lookback must be at least 2 candles")

    if args.command == "live" and args.execute_orders and not args.confirm_live:
        raise ValueError("live order execution requires --confirm-live")

    return symbols


def _write_output(payload: dict[str, object], output_path: str) -> None:
    """Persist backtest results to disk."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


def _run_backtest(args: argparse.Namespace) -> int:
    _validate_backtest_args(args)

    strategy_params = {}
    if args.fast_period is not None:
        strategy_params["fast_period"] = args.fast_period
    if args.slow_period is not None:
        strategy_params["slow_period"] = args.slow_period
    strategy_params.update(_parse_param_args(args.param))

    fetcher = DataFetcher(cache_dir=args.cache_dir)
    runner = BacktestRunner(
        initial_capital=args.capital,
        commission=args.commission,
    )

    df = fetcher.fetch(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        period=args.period,
        interval=args.interval,
        use_cache=not args.no_cache,
    )

    strategy = create_strategy(args.strategy, strategy_params)
    result = runner.run(strategy, df, symbol=args.symbol)

    print(result.summary())

    if args.output:
        payload = {
            "strategy_name": result.strategy_name,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return": result.total_return,
            "total_return_pct": result.total_return_pct,
            "trades": [asdict(trade) for trade in result.trades],
            "metrics": result.calculate_metrics(),
            "start_date": (
                result.start_date.isoformat()
                if isinstance(result.start_date, datetime)
                else str(result.start_date)
            ),
            "end_date": (
                result.end_date.isoformat()
                if isinstance(result.end_date, datetime)
                else str(result.end_date)
            ),
            "equity_curve": result.equity_curve.tolist(),
        }
        _write_output(payload, args.output)
        print(f"Saved results to {args.output}")

    return 0


def _run_list_strategies(args: argparse.Namespace) -> int:
    """Print registered strategies."""
    if getattr(args, "verbose", False):
        for strategy in describe_strategies():
            print(f"{strategy.name}: {strategy.description}")
            if strategy.default_params:
                defaults = ", ".join(
                    f"{key}={value}" for key, value in strategy.default_params.items()
                )
                print(f"  defaults: {defaults}")
        return 0

    print("\n".join(list_strategies()))
    return 0


def _run_init_db(args: argparse.Namespace) -> int:
    """Initialize the configured database schema."""
    result = initialize_database(database_url=args.database_url)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _runtime_strategy_params(args: argparse.Namespace) -> dict[str, object]:
    params = {}
    if args.fast_period is not None:
        params["fast_period"] = args.fast_period
    if args.slow_period is not None:
        params["slow_period"] = args.slow_period
    if args.stop_loss_pct is not None:
        params["stop_loss_pct"] = args.stop_loss_pct
    if args.take_profit_pct is not None:
        params["take_profit_pct"] = args.take_profit_pct
    params.update(_parse_param_args(args.param))
    return params


async def _run_runtime_async(args: argparse.Namespace) -> int:
    symbols = _validate_runtime_args(args)
    runtime = build_runtime_from_config(
        mode=args.command,
        exchange_name=args.exchange,
        strategy_name=args.strategy,
        symbols=symbols,
        interval=args.interval,
        execute_orders=args.execute_orders,
        strategy_params=_runtime_strategy_params(args),
    )
    summary = await runtime.run(
        iterations=args.iterations,
        poll_seconds=args.poll_seconds,
        lookback=args.lookback,
    )
    payload = summary.to_dict()
    print(json.dumps(payload, indent=2))

    if args.output:
        _write_output(payload, args.output)

    return 0


def _run_runtime(args: argparse.Namespace) -> int:
    return asyncio.run(_run_runtime_async(args))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trading workflow CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="Run a backtest")
    backtest_parser.add_argument("--strategy", required=True, help="Strategy name")
    backtest_parser.add_argument("--symbol", required=True, help="Symbol to backtest")
    backtest_parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    backtest_parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    backtest_parser.add_argument(
        "--period", default="1y", help="Period to fetch when no date range is supplied"
    )
    backtest_parser.add_argument("--interval", default="1d", help="Bar interval (default: 1d)")
    backtest_parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")
    backtest_parser.add_argument("--commission", type=float, default=0.001, help="Commission rate")
    backtest_parser.add_argument("--fast-period", type=int, help="Fast SMA period")
    backtest_parser.add_argument("--slow-period", type=int, help="Slow SMA period")
    backtest_parser.add_argument(
        "--param",
        action="append",
        help="Additional strategy parameter as key=value (repeatable)",
    )
    backtest_parser.add_argument(
        "--cache-dir", default="data/cache", help="Local market-data cache directory"
    )
    backtest_parser.add_argument(
        "--no-cache", action="store_true", help="Disable cache reads and writes for this run"
    )
    backtest_parser.add_argument("--output", help="Write result JSON to file")

    list_parser = subparsers.add_parser("list-strategies", help="List available strategies")
    list_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show descriptions and default parameters for each strategy",
    )
    list_parser.set_defaults(func=_run_list_strategies)

    init_db_parser = subparsers.add_parser("init-db", help="Initialize the configured database")
    init_db_parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL for this command (useful for local SQLite smoke tests)",
    )
    init_db_parser.set_defaults(func=_run_init_db)

    def add_runtime_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        runtime_parser = subparsers.add_parser(name, help=help_text)
        runtime_parser.add_argument(
            "--strategy",
            default="sma_crossover",
            help="Top-level async strategy name (currently supported: sma_crossover)",
        )
        runtime_parser.add_argument(
            "--exchange",
            default=get_config().default_exchange,
            choices=["binance", "kraken", "coinbase"],
            help="Exchange adapter to use",
        )
        runtime_parser.add_argument(
            "--symbols",
            nargs="+",
            help="One or more symbols. Defaults to config symbols if omitted.",
        )
        runtime_parser.add_argument("--interval", default="1h", help="Candle interval to poll")
        runtime_parser.add_argument(
            "--iterations",
            type=int,
            default=1,
            help="Number of polling iterations to run",
        )
        runtime_parser.add_argument(
            "--poll-seconds",
            type=float,
            default=60.0,
            help="Seconds to wait between iterations",
        )
        runtime_parser.add_argument(
            "--lookback",
            type=int,
            default=200,
            help="Historical candles to fetch for warmup and signal generation",
        )
        runtime_parser.add_argument(
            "--execute-orders",
            action="store_true",
            help="Submit orders instead of running in signal-only dry-run mode",
        )
        runtime_parser.add_argument(
            "--confirm-live",
            action="store_true",
            help="Required together with --execute-orders for live mode",
        )
        runtime_parser.add_argument("--fast-period", type=int, help="Fast SMA period")
        runtime_parser.add_argument("--slow-period", type=int, help="Slow SMA period")
        runtime_parser.add_argument("--stop-loss-pct", type=float, help="Stop loss percent")
        runtime_parser.add_argument("--take-profit-pct", type=float, help="Take profit percent")
        runtime_parser.add_argument(
            "--param",
            action="append",
            help="Additional strategy parameter as key=value (repeatable)",
        )
        runtime_parser.add_argument("--output", help="Write runtime summary JSON to file")
        runtime_parser.set_defaults(func=_run_runtime)
        return runtime_parser

    add_runtime_parser("paper", "Run the top-level async runtime against sandbox/test environments")
    add_runtime_parser("live", "Run the top-level async runtime against live market endpoints")

    backtest_parser.set_defaults(func=_run_backtest)
    return parser


def main() -> None:
    # Canonical runtime entrypoint: configure logging explicitly here.
    setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    if hasattr(args, "func"):
        try:
            return_code = args.func(args)
        except ValueError as exc:
            parser.error(str(exc))
        raise SystemExit(return_code)

    parser.print_help()
    raise SystemExit(1)
