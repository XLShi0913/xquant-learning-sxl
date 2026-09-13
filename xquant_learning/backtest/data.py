"""Market-data source interfaces used by the simulated broker."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

import pandas as pd


class MarketDataSource(ABC):
    """Load daily bars for one stock id and an inclusive date range."""

    @abstractmethod
    def get_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
        """Return a DataFrame indexed by date with ``Open`` and ``Close``."""


class DataFrameDataSource(MarketDataSource):
    """In-memory source useful for cached notebook data and deterministic tests."""

    def __init__(self, data: Mapping[str, pd.DataFrame]) -> None:
        if not data:
            raise ValueError("data mapping cannot be empty")
        self._data = {symbol: frame.copy() for symbol, frame in data.items()}

    def get_bars(self, symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
        if symbol not in self._data:
            raise KeyError(f"no market data for {symbol}")
        frame = self._data[symbol].copy()
        frame.index = pd.to_datetime(frame.index)
        return frame.sort_index().loc[start:end].copy()
