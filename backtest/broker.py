"""Observed-price execution, commissions and persistent conditional orders."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from .account import Account
from .data import MarketDataSource
from .models import MarketHistory, Order, OrderResult, Trade


class SimBroker:
    """Long-only broker matching orders at one configured daily price sample."""

    def __init__(
        self,
        data_source: MarketDataSource,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        candidates: Iterable[str],
        *,
        execution_price: str = "open",
        commission_rate: float = 0.001,
        minimum_commission: float = 5.0,
    ) -> None:
        if any(not np.isfinite(x) or x < 0 for x in (commission_rate, minimum_commission)):
            raise ValueError("commission parameters must be finite and nonnegative")
        self.commission_rate = float(commission_rate)
        self.minimum_commission = float(minimum_commission)
        if execution_price not in {"open", "close"}:
            raise ValueError("execution_price must be open or close")
        self.execution_price = execution_price
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
        self.excluded_dates = self._open_prices.index[~complete].copy()
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
        self._pending = {}
        self._next_order_id = 1
        self._last_date = None
        self._account = None

    def estimate_commission(self, amount: float) -> float:
        """Fee per fully filled order, on buys and sells, with no rounding."""
        if not np.isfinite(amount) or amount < 0:
            raise ValueError("amount must be finite and nonnegative")
        return max(amount * self.commission_rate, self.minimum_commission) if amount else 0.0

    @property
    def pending_orders(self) -> tuple[OrderResult, ...]:
        """Immutable latest-state snapshots; funds/shares are not reserved."""
        return tuple(item[0] for item in self._pending.values())

    def cancel_order(self, order_id: int) -> OrderResult:
        if order_id not in self._pending:
            raise KeyError(f"no pending order {order_id}")
        previous, _ = self._pending.pop(order_id)
        result = OrderResult(self._last_date, previous.order, "CANCELED", order_id=order_id)
        self.order_history.append(result)
        return result

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

    def history_through(self, date: str | pd.Timestamp) -> MarketHistory:
        """Inclusive history for idealized same-close research, never future bars."""
        dates = self.trading_dates[self.trading_dates <= pd.Timestamp(date)]
        return MarketHistory(self._open_prices.loc[dates].copy(),
                             self._close_prices.loc[dates].copy())

    def execute_orders(
        self,
        account: Account,
        orders: Sequence[Order],
        date: str | pd.Timestamp,
    ) -> list[OrderResult]:
        """Match existing orders, then submit new orders at this price sample.

        GTC orders use only the selected Open/Close sample, not intraday highs
        or lows. Stop-limit activation is latched. Call with [] to advance.
        Unfilled orders survive until filled, rejected, canceled, or reset.
        """
        timestamp = pd.Timestamp(date)
        orders = list(orders)
        if any(not isinstance(order, Order) for order in orders):
            raise TypeError("orders must contain Order objects")
        if self._last_date is not None and timestamp < self._last_date:
            raise ValueError("cannot move broker time backwards")
        if self._account is not None and self._account is not account:
            raise ValueError("reset broker before changing account")
        self._account, self._last_date = account, timestamp
        results = []
        for order_id, (previous, evaluated) in list(self._pending.items()):
            if evaluated == timestamp or timestamp not in self.trading_dates:
                continue
            result = self._match(account, previous.order, timestamp, order_id,
                                 previous.status == "TRIGGERED")
            self._save(result, timestamp)
            results.append(result)
        for order in orders:
            order_id = self._next_order_id
            self._next_order_id += 1
            result = self._match(account, order, timestamp, order_id)
            self._save(result, timestamp)
            results.append(result)
        return results

    def _save(self, result, date):
        self.order_history.append(result)
        if result.status in {"PENDING", "TRIGGERED"}:
            self._pending[result.order_id] = (result, date)
        else:
            self._pending.pop(result.order_id, None)

    def _match(self, account, order, date, order_id, triggered=False):
        def outcome(status, reason=None, trade=None):
            return OrderResult(date, order, status, reason, trade, order_id)
        if order.symbol not in self.candidates:
            return outcome("REJECTED", "symbol is outside candidate universe")
        if order.symbol not in account.positions:
            return outcome("REJECTED", "symbol is outside account universe")
        if date not in self.trading_dates:
            return outcome("REJECTED", "date is not tradable")

        prices = self._open_prices if self.execution_price == "open" else self._close_prices
        price = float(prices.loc[date, order.symbol])
        if order.order_type in {"STOP", "STOP_LIMIT"} and not triggered:
            triggered = price >= order.stop_price if order.side == "BUY" else price <= order.stop_price
            if not triggered:
                return outcome("PENDING")
        if order.order_type in {"LIMIT", "STOP_LIMIT"}:
            marketable = price <= order.limit_price if order.side == "BUY" else price >= order.limit_price
            if not marketable:
                return outcome("TRIGGERED" if triggered else "PENDING")
        fee = self.estimate_commission(order.shares * price)
        if order.side == "BUY":
            if order.shares * price + fee > account.cash + 1e-12:
                return outcome("REJECTED", "insufficient cash")
            account._buy(order.symbol, order.shares, price)
        else:
            if order.shares > account.positions[order.symbol]:
                return outcome("REJECTED", "insufficient shares")
            if account.cash + order.shares * price < fee:
                return outcome("REJECTED", "insufficient cash for sell commission")
            account._sell(order.symbol, order.shares, price)

        account.cash -= fee
        if abs(account.cash) < 1e-12:
            account.cash = 0.0
        trade = Trade(date, order.symbol, order.side, order.shares, price, account.cash, fee, order_id)
        self.trades.append(trade)
        return outcome("FILLED", trade=trade)

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
