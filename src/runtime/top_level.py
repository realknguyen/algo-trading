"""Safe-by-default runtime bridge for top-level async trading components."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from adapters import BinanceAdapter, CoinbaseAdapter, KrakenAdapter, OrderSide, OrderType
from algorithms.base_algorithm import Signal
from algorithms.sma_crossover import SmaCrossoverStrategy
from config.settings import TradingConfig, get_config
from log_config import TradingLogger
from order_management.order_manager import OrderManager, OrderRequest
from risk_management.risk_manager import RiskLimits, RiskManager

SUPPORTED_TOP_LEVEL_STRATEGIES = {"sma_crossover"}
SUPPORTED_EXCHANGES = {"binance", "kraken", "coinbase"}


@dataclass
class RuntimeSummary:
    """Summary of a paper/live polling run."""

    mode: str
    strategy: str
    exchange: str
    symbols: list[str]
    interval: str
    iterations: int
    dry_run: bool
    signals_generated: int
    orders_executed: int
    started_at: datetime
    completed_at: datetime
    last_signal_action: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "strategy": self.strategy,
            "exchange": self.exchange,
            "symbols": self.symbols,
            "interval": self.interval,
            "iterations": self.iterations,
            "dry_run": self.dry_run,
            "signals_generated": self.signals_generated,
            "orders_executed": self.orders_executed,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "last_signal_action": self.last_signal_action,
        }


def _coerce_timestamp(value: Any) -> pd.Timestamp:
    numeric = int(value)
    unit = "ms" if abs(numeric) >= 10**11 else "s"
    return pd.to_datetime(numeric, unit=unit, utc=True).tz_convert(None)


def _candles_to_frame(symbol: str, candles: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for candle in candles:
        records.append(
            {
                "timestamp": _coerce_timestamp(candle["timestamp"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle["volume"]),
                "symbol": symbol,
            }
        )

    if not records:
        raise ValueError(f"No candles returned for {symbol}")

    frame = pd.DataFrame.from_records(records).set_index("timestamp").sort_index()
    return frame


def _build_risk_manager(config: TradingConfig) -> RiskManager:
    limits = RiskLimits(
        max_position_size_pct=config.risk.max_position_size,
        max_drawdown_pct=config.risk.max_drawdown_pct,
        daily_loss_limit=config.risk.daily_loss_limit,
        max_open_positions=config.risk.max_open_positions,
        max_leverage=config.risk.max_leverage,
        stop_loss_default_pct=config.risk.stop_loss_pct,
        take_profit_default_pct=config.risk.take_profit_pct,
    )
    return RiskManager(
        limits=limits,
        initial_capital=Decimal(str(config.initial_capital)),
    )


def _build_order_manager(adapter: Any, risk_manager: RiskManager) -> OrderManager:
    return OrderManager(exchange=adapter, risk_manager=risk_manager)


def _secret_text(secret_like: Any) -> str:
    if secret_like is None:
        return ""
    getter = getattr(secret_like, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(secret_like)


def _coerce_int_param(params: dict[str, object], key: str, default: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float_param(params: dict[str, object], key: str, default: float) -> float:
    value = params.get(key, default)
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_exchange_adapter(
    exchange_name: str,
    config: TradingConfig,
    *,
    sandbox: bool,
) -> Any:
    exchange = exchange_name.lower()
    rate_limit = config.rate_limit_requests_per_second

    if exchange == "binance":
        return BinanceAdapter(
            api_key=_secret_text(config.binance.api_key),
            api_secret=_secret_text(config.binance.api_secret),
            sandbox=sandbox,
            rate_limit_per_second=rate_limit,
        )
    if exchange == "kraken":
        return KrakenAdapter(
            api_key=_secret_text(config.kraken.api_key),
            api_secret=_secret_text(config.kraken.api_secret),
            sandbox=sandbox,
        )
    if exchange == "coinbase":
        return CoinbaseAdapter(
            api_key=_secret_text(config.coinbase.api_key),
            api_secret=_secret_text(config.coinbase.api_secret),
            sandbox=sandbox,
            rate_limit_per_second=rate_limit,
        )

    raise ValueError(
        "Unsupported exchange "
        f"'{exchange_name}'. Available: {', '.join(sorted(SUPPORTED_EXCHANGES))}"
    )


def _require_exchange_credentials(exchange_name: str, config: TradingConfig) -> None:
    exchange = exchange_name.lower()
    if exchange == "binance":
        key = _secret_text(config.binance.api_key)
        secret = _secret_text(config.binance.api_secret)
    elif exchange == "kraken":
        key = _secret_text(config.kraken.api_key)
        secret = _secret_text(config.kraken.api_secret)
    elif exchange == "coinbase":
        key = _secret_text(config.coinbase.api_key)
        secret = _secret_text(config.coinbase.api_secret)
    else:
        raise ValueError(f"Unsupported exchange '{exchange_name}'")

    if not key or not secret:
        raise ValueError(
            f"{exchange_name} credentials are required when --execute-orders is enabled"
        )


def _build_algorithm(
    strategy_name: str,
    symbols: list[str],
    *,
    order_manager: Optional[OrderManager],
    risk_manager: RiskManager,
    strategy_params: Optional[dict[str, object]] = None,
) -> Any:
    params = strategy_params or {}
    name = strategy_name.lower()

    if name == "sma_crossover":
        return SmaCrossoverStrategy(
            symbols=symbols,
            fast_period=_coerce_int_param(params, "fast_period", 20),
            slow_period=_coerce_int_param(params, "slow_period", 50),
            stop_loss_pct=_coerce_float_param(params, "stop_loss_pct", 2.0),
            take_profit_pct=_coerce_float_param(params, "take_profit_pct", 4.0),
            order_manager=order_manager,
            risk_manager=risk_manager,
        )

    raise ValueError(
        "Unsupported top-level strategy "
        f"'{strategy_name}'. Available: {', '.join(sorted(SUPPORTED_TOP_LEVEL_STRATEGIES))}"
    )


class PollingTradingRuntime:
    """Polling runtime that bridges the CLI to the top-level async trading stack."""

    def __init__(
        self,
        *,
        mode: str,
        exchange_name: str,
        interval: str,
        symbols: list[str],
        adapter: Any,
        algorithm: Any,
        order_manager: Optional[OrderManager],
        execute_orders: bool,
        logger: TradingLogger | None = None,
    ):
        self.mode = mode
        self.exchange_name = exchange_name
        self.interval = interval
        self.symbols = symbols
        self.adapter = adapter
        self.algorithm = algorithm
        self.order_manager = order_manager
        self.execute_orders = execute_orders
        self.logger = logger or TradingLogger("PollingTradingRuntime")

    async def _fetch_market_data(self, lookback: int) -> dict[str, pd.DataFrame]:
        datasets: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            candles = await self.adapter.get_historical_candles(
                symbol=symbol,
                interval=self.interval,
                limit=lookback,
            )
            datasets[symbol] = _candles_to_frame(symbol, candles)
        return datasets

    async def _run_signal_cycle(
        self, market_data: dict[str, pd.DataFrame]
    ) -> tuple[Optional[Signal], int]:
        self.algorithm.data = market_data
        for symbol, frame in market_data.items():
            if not frame.empty:
                self.algorithm.current_prices[symbol] = Decimal(str(frame["close"].iloc[-1]))

        signal = self.algorithm.on_data(market_data)
        executed_orders = 0

        if signal is None:
            return None, executed_orders

        self.algorithm.signal_count += 1
        self.algorithm.signals.append(signal)
        self.algorithm.logger.signal(
            self.algorithm.config.name,
            signal.symbol,
            signal.action,
            signal.confidence,
            signal.metadata,
        )

        if self.execute_orders and signal.action in {"buy", "sell"}:
            order = await self.algorithm.on_execute(signal)
            if order is not None:
                executed_orders += 1
        elif signal.action in {"buy", "sell"}:
            self.logger.info(
                "Signal generated in dry-run mode; no order submitted",
                mode=self.mode,
                symbol=signal.symbol,
                action=signal.action,
                confidence=signal.confidence,
            )

        return signal, executed_orders

    async def run(self, *, iterations: int, poll_seconds: float, lookback: int) -> RuntimeSummary:
        started_at = datetime.now(timezone.utc)
        signals_generated = 0
        orders_executed = 0
        last_signal_action: Optional[str] = None

        if self.execute_orders:
            await self.adapter.connect()
            if self.order_manager is not None:
                await self.order_manager.start()

        try:
            initial_data = await self._fetch_market_data(lookback)
            self.algorithm.initialize(initial_data)
            await self.algorithm.start()

            for iteration in range(iterations):
                market_data = await self._fetch_market_data(lookback)
                signal, executed = await self._run_signal_cycle(market_data)
                orders_executed += executed

                if signal is not None:
                    signals_generated += 1
                    last_signal_action = signal.action

                if iteration < iterations - 1 and poll_seconds > 0:
                    await asyncio.sleep(poll_seconds)
        finally:
            try:
                await self.algorithm.stop()
            finally:
                if self.order_manager is not None and self.execute_orders:
                    await self.order_manager.stop()
                if self.execute_orders:
                    await self.adapter.disconnect()

        completed_at = datetime.now(timezone.utc)
        return RuntimeSummary(
            mode=self.mode,
            strategy=self.algorithm.config.name,
            exchange=self.exchange_name,
            symbols=self.symbols,
            interval=self.interval,
            iterations=iterations,
            dry_run=not self.execute_orders,
            signals_generated=signals_generated,
            orders_executed=orders_executed,
            started_at=started_at,
            completed_at=completed_at,
            last_signal_action=last_signal_action,
        )


def build_runtime_from_config(
    *,
    mode: str,
    exchange_name: str,
    strategy_name: str,
    symbols: list[str],
    interval: str,
    execute_orders: bool,
    strategy_params: Optional[dict[str, object]] = None,
    config: Optional[TradingConfig] = None,
) -> PollingTradingRuntime:
    """Build a polling runtime using top-level exchange/algorithm services."""
    resolved_config = config or get_config()
    normalized_mode = mode.lower()
    sandbox = normalized_mode != "live"

    if execute_orders:
        _require_exchange_credentials(exchange_name, resolved_config)

    adapter = _build_exchange_adapter(
        exchange_name=exchange_name,
        config=resolved_config,
        sandbox=sandbox,
    )
    risk_manager = _build_risk_manager(resolved_config)
    order_manager = _build_order_manager(adapter, risk_manager) if execute_orders else None
    algorithm = _build_algorithm(
        strategy_name=strategy_name,
        symbols=symbols,
        order_manager=order_manager,
        risk_manager=risk_manager,
        strategy_params=strategy_params,
    )

    return PollingTradingRuntime(
        mode=normalized_mode,
        exchange_name=exchange_name.lower(),
        interval=interval,
        symbols=symbols,
        adapter=adapter,
        algorithm=algorithm,
        order_manager=order_manager,
        execute_orders=execute_orders,
    )


def build_order_request(
    *,
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
) -> OrderRequest:
    """Helper primarily for tests that need a top-level order request."""
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
    return OrderRequest(
        symbol=symbol,
        side=order_side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        reference_price=price,
    )
