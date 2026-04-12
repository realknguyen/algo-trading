"""Simple CLI for common trading workflows."""

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from typing import Dict, Optional

from src.backtest.runner import BacktestRunner
from src.data.fetcher import DataFetcher
from src.strategy import create_strategy, list_strategies


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


def _run_backtest(args: argparse.Namespace) -> int:
    strategy_params = {}
    if args.fast_period is not None:
        strategy_params["fast_period"] = args.fast_period
    if args.slow_period is not None:
        strategy_params["slow_period"] = args.slow_period
    strategy_params.update(_parse_param_args(args.param))

    fetcher = DataFetcher()
    runner = BacktestRunner(
        initial_capital=args.capital,
        commission=args.commission,
    )

    df = fetcher.fetch(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        interval=args.interval,
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
            "start_date": result.start_date.isoformat() if isinstance(result.start_date, datetime) else str(result.start_date),
            "end_date": result.end_date.isoformat() if isinstance(result.end_date, datetime) else str(result.end_date),
            "equity_curve": result.equity_curve.tolist(),
        }
        with open(args.output, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
        print(f"Saved results to {args.output}")

    return 0


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
    backtest_parser.add_argument("--output", help="Write result JSON to file")

    list_parser = subparsers.add_parser("list-strategies", help="List available strategies")
    list_parser.set_defaults(func=lambda _args: print("\n".join(list_strategies())) or 0)

    backtest_parser.set_defaults(func=_run_backtest)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if hasattr(args, "func"):
        return_code = args.func(args)
        raise SystemExit(return_code)

    parser.print_help()
    raise SystemExit(1)

