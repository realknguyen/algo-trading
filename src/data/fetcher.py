"""Data ingestion and local caching helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from log_config import TradingLogger


class DataFetcher:
    """Fetch and cache OHLCV market data."""

    def __init__(
        self,
        cache_dir: str = "data/cache",
        logger: TradingLogger | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or TradingLogger("DataFetcher")

    @staticmethod
    def _sanitize_cache_component(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
        return normalized.strip("-") or "default"

    def _build_cache_key(
        self,
        *,
        start: Optional[str],
        end: Optional[str],
        period: str,
    ) -> str:
        if start or end:
            start_key = self._sanitize_cache_component(start or "open")
            end_key = self._sanitize_cache_component(end or "latest")
            return f"range-{start_key}-to-{end_key}"
        return f"period-{self._sanitize_cache_component(period)}"

    def _cache_path(
        self,
        *,
        symbol: str,
        start: Optional[str],
        end: Optional[str],
        period: str,
        interval: str,
    ) -> Path:
        cache_key = self._build_cache_key(start=start, end=end, period=period)
        safe_symbol = self._sanitize_cache_component(symbol)
        safe_interval = self._sanitize_cache_component(interval)
        return self.cache_dir / f"{safe_symbol}_{cache_key}_{safe_interval}.csv"

    def _legacy_cache_path(
        self,
        *,
        symbol: str,
        start: Optional[str],
        end: Optional[str],
        period: str,
        interval: str,
    ) -> Path:
        cache_key = f"{start}_{end}" if start and end else period
        return self.cache_dir / f"{symbol}_{cache_key}__{interval}.csv"

    @staticmethod
    def _normalize_frame(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        normalized = df.copy()
        normalized.columns = [str(col).lower().replace(" ", "_") for col in normalized.columns]
        normalized.sort_index(inplace=True)
        normalized["symbol"] = symbol
        return normalized

    @staticmethod
    def _validate_date_range(start: Optional[str], end: Optional[str]) -> None:
        if not start or not end:
            return

        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if start_ts > end_ts:
            raise ValueError("start date must be on or before end date")

    def _load_cached_frame(
        self, symbol: str, candidate_paths: list[Path]
    ) -> Optional[pd.DataFrame]:
        seen: set[Path] = set()
        for path in candidate_paths:
            if path in seen or not path.exists():
                continue
            seen.add(path)

            try:
                cached = pd.read_csv(path, index_col=0, parse_dates=True)
            except Exception as exc:
                self.logger.warning(
                    "Ignoring unreadable cache file and refetching data",
                    symbol=symbol,
                    cache_path=str(path),
                    error=str(exc),
                )
                continue

            self.logger.info(
                "Loaded market data from cache",
                symbol=symbol,
                cache_path=str(path),
                rows=len(cached),
            )
            return self._normalize_frame(cached, symbol)

        return None

    def fetch(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: str = "1y",
        interval: str = "1d",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch market data for a symbol."""
        self._validate_date_range(start, end)

        cache_path = self._cache_path(
            symbol=symbol,
            start=start,
            end=end,
            period=period,
            interval=interval,
        )
        legacy_cache_path = self._legacy_cache_path(
            symbol=symbol,
            start=start,
            end=end,
            period=period,
            interval=interval,
        )

        if use_cache:
            cached = self._load_cached_frame(symbol, [cache_path, legacy_cache_path])
            if cached is not None:
                return cached

        self.logger.info(
            "Fetching market data from Yahoo Finance",
            symbol=symbol,
            start=start,
            end=end,
            period=period,
            interval=interval,
        )

        ticker = yf.Ticker(symbol)
        history_kwargs = {"interval": interval}
        if start or end:
            if start:
                history_kwargs["start"] = start
            if end:
                history_kwargs["end"] = end
        else:
            history_kwargs["period"] = period

        df = ticker.history(**history_kwargs)
        if df.empty:
            raise ValueError(f"No data found for {symbol}")

        normalized = self._normalize_frame(df, symbol)

        if use_cache:
            normalized.to_csv(cache_path)
            self.logger.info(
                "Cached market data locally",
                symbol=symbol,
                cache_path=str(cache_path),
                rows=len(normalized),
            )

        return normalized

    def clear_cache(self, symbol: Optional[str] = None) -> None:
        """Clear cached data, optionally scoped to one symbol."""
        if symbol:
            safe_symbol = self._sanitize_cache_component(symbol)
            candidates = [
                path
                for path in self.cache_dir.glob("*.csv")
                if path.name.startswith(f"{safe_symbol}_") or path.name.startswith(f"{symbol}_")
            ]
        else:
            candidates = list(self.cache_dir.glob("*.csv"))

        removed = 0
        for path in candidates:
            path.unlink(missing_ok=True)
            removed += 1

        self.logger.info(
            "Cleared cached market data",
            symbol=symbol or "*",
            removed_files=removed,
        )


def fetch_multiple(
    symbols: list[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "1y",
    interval: str = "1d",
    use_cache: bool = True,
    cache_dir: str = "data/cache",
) -> dict[str, pd.DataFrame]:
    """Fetch data for multiple symbols, skipping failures."""
    fetcher = DataFetcher(cache_dir=cache_dir)
    data: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        try:
            data[symbol] = fetcher.fetch(
                symbol,
                start=start,
                end=end,
                period=period,
                interval=interval,
                use_cache=use_cache,
            )
        except Exception as exc:
            fetcher.logger.error(
                "fetch_multiple",
                f"Failed to fetch market data for {symbol}",
                exception=exc,
                symbol=symbol,
            )

    return data


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    raise SystemExit("Use `python main.py backtest ...` or import DataFetcher directly.")
