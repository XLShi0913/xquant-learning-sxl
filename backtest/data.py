"""Market-data source interfaces used by the simulated broker."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
import pandas as pd
from .configuration import load_config


def date_range(start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start) or pd.isna(end) or start.tzinfo or end.tzinfo or start > end:
        raise ValueError("dates must be valid timezone-naive values with start <= end")
    return start.normalize(), end.normalize()


def validate_bars(frame):
    """Validate daily, local-calendar bars rather than silently fill missing prices."""
    result = frame.copy()
    result.index = pd.DatetimeIndex(pd.to_datetime(result.index))
    if result.index.tz is not None or result.index.hasnans or result.index.has_duplicates:
        raise ValueError("daily bar dates must be timezone-naive, valid and unique")
    if not result.index.equals(result.index.normalize()):
        raise ValueError("daily bars must be indexed by local calendar date")
    if not {"Open", "Close"}.issubset(result.columns):
        raise ValueError("bars require Open and Close")
    prices = result[["Open", "Close"]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError("Open/Close must be finite and positive")
    result[["Open", "Close"]] = prices
    return result.sort_index().rename_axis("Date")


class MarketDataSource(ABC):
    """Load daily bars for one stock id and an inclusive date range."""

    @abstractmethod
    def get_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
        """Return a DataFrame indexed by date with ``Open`` and ``Close``."""

    def window(self, start, end):
        return WindowDataSource(self, start, end)

    def split(self, *, train_start, train_end, validation_start, validation_end):
        """Disjoint bounded views; no pre-training warmup or validation leakage."""
        a, b = date_range(train_start, train_end)
        c, d = date_range(validation_start, validation_end)
        if b >= c:
            raise ValueError("training and validation date ranges must not overlap")
        return DataSplit(self.window(a, b), self.window(c, d))


@dataclass(frozen=True)
class DataSplit:
    train: "WindowDataSource"
    validation: "WindowDataSource"


class WindowDataSource(MarketDataSource):
    def __init__(self, source, start, end):
        self.source = source
        self.start, self.end = date_range(start, end)

    def get_bars(self, symbol, start, end):
        a, b = date_range(start, end)
        a, b = max(a, self.start), min(b, self.end)
        if a > b:
            return pd.DataFrame(columns=["Open", "Close"], index=pd.DatetimeIndex([], name="Date"))
        return self.source.get_bars(symbol, a, b).loc[a:b].copy()


class DataFrameDataSource(MarketDataSource):
    """In-memory source useful for cached notebook data and deterministic tests."""

    def __init__(self, data: Mapping[str, pd.DataFrame]) -> None:
        if not data:
            raise ValueError("data mapping cannot be empty")
        self._data = {symbol: validate_bars(frame) for symbol, frame in data.items()}

    def get_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
        if symbol not in self._data:
            raise KeyError(f"no market data for {symbol}")
        start, end = date_range(start, end)
        return self._data[symbol].loc[start:end].copy()


class CachedCsvDataSource(MarketDataSource):
    """Read course CSV caches; retain the opt-in Yahoo fallback for compatibility."""
    def __init__(self, root=None, *, config=None, cache_dirs=None, price_mode="adjusted"):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.config = load_config(config)
        settings = self.config.section("data")
        if price_mode not in {"adjusted", "raw"}:
            raise ValueError("price_mode must be adjusted or raw")
        self.price_mode = price_mode
        self.download_if_missing = settings["download_if_missing"]
        self.cache_dirs = tuple(self.root / p for p in (
            settings["cache_dirs"] if cache_dirs is None else cache_dirs))
        if not self.cache_dirs:
            raise ValueError("cache_dirs cannot be empty")
        self._loaded = {}

    def get_bars(self, symbol, start, end):
        start, end = date_range(start, end)
        candidates = []
        for directory in self.cache_dirs:
            for path in sorted(directory.glob(f"{symbol}_*_{self.price_mode}.csv")):
                match = re.fullmatch(re.escape(symbol) + r"_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_" + self.price_mode + r"\.csv", path.name)
                if match and pd.Timestamp(match[1]) <= start and pd.Timestamp(match[2]) >= end:
                    candidates.append(path)
        if not candidates:
            if self.download_if_missing:
                from .yahoo import YFinanceDataSource
                return YFinanceDataSource(self.root, config=self.config,
                    cache_dirs=self.cache_dirs, cache_dir=self.cache_dirs[-1],
                    auto_adjust=self.price_mode == "adjusted").get_bars(symbol, start, end)
            raise FileNotFoundError(f"no {self.price_mode} cache covers {symbol} {start.date()}..{end.date()}; download explicitly first")
        # Stable choice across directories. Each file is parsed once per source.
        path = candidates[0]
        if path not in self._loaded:
            self._loaded[path] = validate_bars(pd.read_csv(path, index_col=0, parse_dates=True))
        result = self._loaded[path].loc[start:end].copy()
        if result.empty:
            raise ValueError(f"cache contains no bars for {symbol} in requested window")
        return result
