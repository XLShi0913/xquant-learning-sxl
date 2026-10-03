"""Yahoo daily bars through yfinance, with compatible local CSV caching."""
from pathlib import Path
import math

import pandas as pd

from .configuration import UNSET, configured, load_config
from .data import CachedCsvDataSource, MarketDataSource, date_range, validate_bars


class YFinanceDataSource(MarketDataSource):
    """Cache first; explicit permission is required for a missing-data download.

    Daily dates include both endpoints. No implicit switch to another provider.
    ``downloader`` allows offline tests without importing or contacting Yahoo.
    """
    def __init__(self, root=None, *, config=None, cache_dirs=None,
                 cache_dir=UNSET, auto_adjust=UNSET, timeout=UNSET,
                 threads=UNSET, progress=UNSET, download_if_missing=UNSET,
                 downloader=None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.config = load_config(config)
        data = self.config.section("data")
        settings = data["yfinance"]
        self.auto_adjust = configured(auto_adjust, settings, "auto_adjust")
        self.timeout = configured(timeout, settings, "timeout")
        self.threads = configured(threads, settings, "threads")
        self.progress = configured(progress, settings, "progress")
        self.download_if_missing = configured(download_if_missing, data, "download_if_missing")
        for name in ("auto_adjust", "threads", "progress", "download_if_missing"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("timeout must be a positive finite number")
        self.cache_dir = self.root / configured(cache_dir, settings, "cache_dir")
        self.price_mode = "adjusted" if self.auto_adjust else "raw"
        directories = data["cache_dirs"] if cache_dirs is None else cache_dirs
        directories = tuple(dict.fromkeys([self.root / p for p in directories] + [self.cache_dir]))
        # The reader must not invoke its legacy download fallback recursively.
        self.cache = CachedCsvDataSource(self.root,
            config=self.config.override(data={"download_if_missing": False}),
            cache_dirs=directories, price_mode=self.price_mode)
        self._downloader = downloader

    def get_bars(self, symbol, start, end):
        start, end = date_range(start, end)
        try:
            return self.cache.get_bars(symbol, start, end)
        except FileNotFoundError:
            if not self.download_if_missing:
                raise
        downloader = self._downloader
        if downloader is None:
            import yfinance as yf
            downloader = yf.download
        # Yahoo's end is exclusive; our common data-source contract is inclusive.
        raw = downloader(symbol, start=start.strftime("%Y-%m-%d"),
            end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), interval="1d",
            auto_adjust=self.auto_adjust, multi_level_index=False, ignore_tz=True,
            threads=self.threads, progress=self.progress, timeout=self.timeout)
        if raw is None or raw.empty:
            raise RuntimeError(f"{symbol}: yfinance download failed; no usable cache")
        result = validate_bars(raw).loc[start:end].copy()
        if result.empty:
            raise ValueError(f"{symbol}: download has no bars inside requested dates")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{symbol}_{start.date()}_{end.date()}_{self.price_mode}.csv"
        result.to_csv(path)
        return result.copy()
