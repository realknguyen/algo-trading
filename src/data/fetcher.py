"""Data ingestion and caching module."""

import os
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import yfinance as yf


class DataFetcher:
    """Fetch and cache market data from various sources."""
    
    def __init__(self, cache_dir: str = "data/cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_path(self, symbol: str, start: str, end: str, interval: str) -> str:
        """Generate cache file path."""
        filename = f"{symbol}_{start}_{end}_{interval}.csv"
        return os.path.join(self.cache_dir, filename)
    
    def fetch(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: str = "1y",
        interval: str = "1d",
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        Fetch market data for a symbol.
        
        Args:
            symbol: Stock ticker symbol
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            period: Period to fetch if start/end not provided (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
            use_cache: Whether to use cached data
        
        Returns:
            DataFrame with OHLCV data
        """
        # Determine date range for caching
        if start and end:
            cache_key = f"{start}_{end}"
        else:
            cache_key = period
        
        cache_path = self._get_cache_path(symbol, cache_key, "", interval)
        
        # Try to load from cache
        if use_cache and os.path.exists(cache_path):
            print(f"Loading {symbol} from cache...")
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            return df
        
        # Fetch from yfinance
        print(f"Fetching {symbol} data from Yahoo Finance...")
        ticker = yf.Ticker(symbol)
        
        if start and end:
            df = ticker.history(start=start, end=end, interval=interval)
        else:
            df = ticker.history(period=period, interval=interval)
        
        if df.empty:
            raise ValueError(f"No data found for {symbol}")
        
        # Standardize column names to lowercase
        df.columns = [col.lower().replace(' ', '_') for col in df.columns]
        df['symbol'] = symbol
        
        # Save to cache
        if use_cache:
            df.to_csv(cache_path)
            print(f"Cached data to {cache_path}")
        
        return df
    
    def clear_cache(self, symbol: Optional[str] = None) -> None:
        """Clear cached data. If symbol provided, only clear that symbol's cache."""
        if symbol:
            for file in os.listdir(self.cache_dir):
                if file.startswith(symbol):
                    os.remove(os.path.join(self.cache_dir, file))
                    print(f"Removed cache: {file}")
        else:
            for file in os.listdir(self.cache_dir):
                if file.endswith('.csv'):
                    os.remove(os.path.join(self.cache_dir, file))
            print("Cache cleared")


def fetch_multiple(
    symbols: list[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "1y",
    interval: str = "1d"
) -> dict[str, pd.DataFrame]:
    """Fetch data for multiple symbols."""
    fetcher = DataFetcher()
    data = {}
    
    for symbol in symbols:
        try:
            df = fetcher.fetch(symbol, start=start, end=end, period=period, interval=interval)
            data[symbol] = df
        except Exception as e:
            print(f"Error fetching {symbol}: {e}")
    
    return data


if __name__ == "__main__":
    # Example usage
    fetcher = DataFetcher()
    
    # Fetch AAPL data
    df = fetcher.fetch("AAPL", period="1y", interval="1d")
    print(f"\nFetched {len(df)} rows for AAPL")
    print(df.head())
    
    # Fetch multiple symbols
    symbols = ["AAPL", "MSFT", "GOOGL"]
    data = fetch_multiple(symbols, period="6mo")
    print(f"\nFetched data for {len(data)} symbols")
