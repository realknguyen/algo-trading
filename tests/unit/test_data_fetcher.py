"""Unit tests for market-data fetching and cache behavior."""

from __future__ import annotations

import pandas as pd

from src.data.fetcher import DataFetcher, fetch_multiple


class _FakeTicker:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def history(self, **_kwargs):
        return self._frame.copy()


def test_fetch_reads_legacy_cache_file_without_network(tmp_path, monkeypatch):
    """Existing cache files should remain readable after cache-key cleanup."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    dates = pd.date_range("2023-01-01", periods=3, freq="D")
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1000, 1100, 1200],
            "symbol": ["TEST", "TEST", "TEST"],
        },
        index=dates,
    )
    frame.to_csv(cache_dir / "TEST_1y__1d.csv")

    def _unexpected_ticker(_symbol):
        raise AssertionError("network fetch should not run when legacy cache exists")

    monkeypatch.setattr("src.data.fetcher.yf.Ticker", _unexpected_ticker)

    fetcher = DataFetcher(cache_dir=str(cache_dir))
    result = fetcher.fetch("TEST", period="1y", use_cache=True)

    assert len(result) == 3
    assert result["symbol"].iloc[0] == "TEST"


def test_fetch_normalizes_remote_data_and_writes_canonical_cache(tmp_path, monkeypatch):
    """Fresh fetches should normalize data and use the new stable cache naming."""
    raw = pd.DataFrame(
        {
            "Open": [101.0, 100.0],
            "High": [102.0, 101.0],
            "Low": [99.0, 98.0],
            "Close": [100.5, 99.5],
            "Volume": [1500, 1400],
        },
        index=pd.to_datetime(["2023-01-02", "2023-01-01"]),
    )

    monkeypatch.setattr("src.data.fetcher.yf.Ticker", lambda _symbol: _FakeTicker(raw))

    fetcher = DataFetcher(cache_dir=str(tmp_path))
    result = fetcher.fetch(
        "BTC/USD",
        start="2023-01-01",
        end="2023-01-02",
        interval="1d",
        use_cache=True,
    )

    cache_path = tmp_path / "BTC-USD_range-2023-01-01-to-2023-01-02_1d.csv"

    assert cache_path.exists()
    assert list(result.columns) == ["open", "high", "low", "close", "volume", "symbol"]
    assert result.index.is_monotonic_increasing
    assert result["symbol"].iloc[0] == "BTC/USD"


def test_fetch_multiple_skips_failed_symbols(monkeypatch):
    """Batch fetches should keep successful symbols even if one fetch fails."""
    good_frame = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000],
        },
        index=pd.to_datetime(["2023-01-01"]),
    )

    def _fake_ticker(symbol):
        if symbol == "FAIL":
            raise RuntimeError("boom")
        return _FakeTicker(good_frame)

    monkeypatch.setattr("src.data.fetcher.yf.Ticker", _fake_ticker)

    result = fetch_multiple(["AAPL", "FAIL"], use_cache=False)

    assert list(result.keys()) == ["AAPL"]
