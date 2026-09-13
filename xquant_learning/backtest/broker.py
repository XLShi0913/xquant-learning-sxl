"""Historical price storage and opening-price order execution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from .account import Account
from .data import MarketDataSource
from .models import MarketHistory, Order, OrderResult, Trade


class SimBroker:
    """Load a candidate universe and simulate long-only trades at daily open."""

    def __init__(
        self,
        data_source: MarketDataSource,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        candidates: Iterable[str],
    ) -> None:
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        if self.start > self.end:
            raise ValueError("start cannot be later than end")
        self.candidates = tuple(dict.fromkeys(candidates))
        if not self.candidates or any(not symbol for symbol in self.candidates):
            raise ValueError("candidates must contain at least one non-empty id")

        bars = {
            symbol: self._validated_bars(
                data_source.get_bars(symbol, self.start, self.end), symbol
            )
            for symbol in self.candidates
        }
        self._open_prices = pd.concat(
            {symbol: frame["Open"] for symbol, frame in bars.items()}, axis=1
        ).sort_index()
        self._close_prices = pd.concat(
            {symbol: frame["Close"] for symbol, frame in bars.items()}, axis=1
        ).sort_index()

        complete = self._open_prices.notna().all(axis=1) & self._close_prices.notna().all(axis=1)
        self.trading_dates = pd.DatetimeIndex(self._open_prices.index[complete])
        if self.trading_dates.empty:
            raise ValueError("candidates have no common complete Open/Close trading dates")
        self._open_prices = self._open_prices.loc[self.trading_dates]
        self._close_prices = self._close_prices.loc[self.trading_dates]
        self.reset()

    @property
    def open_prices(self) -> pd.DataFrame:
        """Return a copy of the broker's aligned opening-price list."""
        return self._open_prices.copy()

    @property
    def close_prices(self) -> pd.DataFrame:
        """Return a copy of the broker's aligned closing-price list."""
        return self._close_prices.copy()

    def reset(self) -> None:
        """Clear executions while keeping downloaded price data."""
        self.trades: list[Trade] = []
        self.order_history: list[OrderResult] = []

    def history_before(self, date: str | pd.Timestamp, lookback: int | None = None) -> MarketHistory:
        """Return prices strictly before ``date`` so strategies cannot look ahead."""
        if lookback is not None and (
            isinstance(lookback, bool) or not isinstance(lookback, int) or lookback <= 0
        ):
            raise ValueError("lookback must be a positive integer or None")
        dates = self.trading_dates[self.trading_dates < pd.Timestamp(date)]
        if lookback is not None:
            dates = dates[-lookback:]
        return MarketHistory(
            open=self._open_prices.loc[dates].copy(),
            close=self._close_prices.loc[dates].copy(),
        )

    def close_prices_at(self, date: str | pd.Timestamp) -> dict[str, float]:
        timestamp = pd.Timestamp(date)
        if timestamp not in self.trading_dates:
            raise KeyError(f"{timestamp.date()} is not a common trading date")
        row = self._close_prices.loc[timestamp]
        return {symbol: float(row[symbol]) for symbol in self.candidates}

    def execute_orders(
        self,
        account: Account,
        orders: Sequence[Order],
        date: str | pd.Timestamp,
    ) -> list[OrderResult]:
        """Execute orders sequentially at the day's opening prices."""
        timestamp = pd.Timestamp(date)
        results = [self._execute_one(account, order, timestamp) for order in orders]
        self.order_history.extend(results)
        return results

    def _execute_one(self, account: Account, order: Order, date: pd.Timestamp) -> OrderResult:
        if not isinstance(order, Order):
            raise TypeError("strategy must return Order objects")
        if order.symbol not in self.candidates:
            return OrderResult(date, order, "REJECTED", "symbol is outside candidate universe")
        if order.symbol not in account.positions:
            return OrderResult(date, order, "REJECTED", "symbol is outside account universe")
        if date not in self.trading_dates:
            return OrderResult(date, order, "REJECTED", "date is not tradable")

        price = float(self._open_prices.loc[date, order.symbol])
        if order.side == "BUY":
            if order.shares * price > account.cash + 1e-12:
                return OrderResult(date, order, "REJECTED", "insufficient cash")
            account._buy(order.symbol, order.shares, price)
        else:
            if order.shares > account.positions[order.symbol]:
                return OrderResult(date, order, "REJECTED", "insufficient shares")
            account._sell(order.symbol, order.shares, price)

        if abs(account.cash) < 1e-12:
            account.cash = 0.0
        trade = Trade(date, order.symbol, order.side, order.shares, price, account.cash)
        self.trades.append(trade)
        return OrderResult(date, order, "FILLED", trade=trade)

    def _validated_bars(self, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if frame.empty:
            raise ValueError(f"{symbol} returned no market data")
        missing = {"Open", "Close"}.difference(frame.columns)
        if missing:
            raise ValueError(f"{symbol} is missing columns: {sorted(missing)}")
        result = frame[["Open", "Close"]].copy()
        result.index = pd.to_datetime(result.index)
        result = result.sort_index().loc[self.start : self.end]
        if result.empty:
            raise ValueError(f"{symbol} has no data in the requested range")
        if result.index.has_duplicates or result.index.hasnans:
            raise ValueError(f"{symbol} dates must be unique and valid")
        for column in ("Open", "Close"):
            result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
            valid = np.isfinite(result[column]) & (result[column] > 0)
            result.loc[~valid, column] = np.nan
        return result
