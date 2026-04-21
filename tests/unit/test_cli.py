"""Unit tests for CLI argument handling."""

import argparse

import pytest

from src.cli import (
    _parse_param_args,
    _run_list_strategies,
    _validate_backtest_args,
    _validate_runtime_args,
)


def test_parse_param_args_coerces_common_scalar_types():
    """CLI strategy params should be parsed into basic scalar types."""
    parsed = _parse_param_args(
        [
            "flag=true",
            "count=3",
            "ratio=1.5",
            "name=alpha",
        ]
    )

    assert parsed == {
        "flag": True,
        "count": 3,
        "ratio": 1.5,
        "name": "alpha",
    }


def test_validate_backtest_args_requires_complete_date_range():
    """Date-bounded backtests should require both boundaries."""
    args = argparse.Namespace(
        start="2024-01-01",
        end=None,
        capital=100000.0,
        commission=0.001,
    )

    with pytest.raises(ValueError, match="Provide both --start and --end together"):
        _validate_backtest_args(args)


def test_validate_backtest_args_rejects_inverted_date_range():
    """Backtest dates should be ordered chronologically."""
    args = argparse.Namespace(
        start="2024-02-01",
        end="2024-01-01",
        capital=100000.0,
        commission=0.001,
    )

    with pytest.raises(ValueError, match="start date must be on or before end date"):
        _validate_backtest_args(args)


def test_validate_backtest_args_rejects_invalid_financial_inputs():
    """Capital and commission should be validated before execution."""
    args = argparse.Namespace(
        start=None,
        end=None,
        capital=0.0,
        commission=-0.1,
    )

    with pytest.raises(ValueError, match="capital must be positive"):
        _validate_backtest_args(args)


def test_validate_runtime_args_requires_live_confirmation_for_execution():
    """Live order execution must require an explicit confirmation flag."""
    args = argparse.Namespace(
        command="live",
        symbols=["BTCUSDT"],
        iterations=1,
        poll_seconds=0.0,
        lookback=50,
        execute_orders=True,
        confirm_live=False,
    )

    with pytest.raises(ValueError, match="requires --confirm-live"):
        _validate_runtime_args(args)


def test_run_list_strategies_verbose_prints_descriptions_and_defaults(capsys):
    """Verbose strategy listing should help newcomers discover available knobs."""
    args = argparse.Namespace(verbose=True)

    result = _run_list_strategies(args)

    captured = capsys.readouterr()
    assert result == 0
    assert "sma_crossover: Simple Moving Average Crossover Strategy." in captured.out
    assert "defaults: fast_period=20, slow_period=50" in captured.out
    assert "sma_crossover_risk: SMA crossover strategy with simple volatility and stop metadata." in captured.out
