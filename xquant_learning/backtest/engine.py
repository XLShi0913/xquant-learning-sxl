"""Daily orchestration and account-history statistics."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .account import Account
from .broker import SimBroker
from .models import OrderResult
from .strategy import Strategy


@dataclass(slots=True)
class Engine:
    """Run one strategy against one broker and one account."""

    account: Account
    broker: SimBroker
    strategy: Strategy
    annual_trading_days: int = 252
    daily_order_results: dict[pd.Timestamp, list[OrderResult]] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.annual_trading_days, bool)
            or not isinstance(self.annual_trading_days, int)
            or self.annual_trading_days <= 0
        ):
            raise ValueError("annual_trading_days must be a positive integer")
        if tuple(self.account.symbols) != tuple(self.broker.candidates):
            raise ValueError("account symbols and broker candidates must match in order")

    def run(self) -> pd.DataFrame:
        """Simulate each common trading day and return end-of-day account history."""
        self.account.reset()
        self.broker.reset()
        self.strategy.reset()
        self.daily_order_results = {}

        for date in self.broker.trading_dates:
            market = self.broker.history_before(date)
            orders = list(self.strategy.generate_orders(self.account.view(), market, date))
            self.daily_order_results[date] = self.broker.execute_orders(
                self.account, orders, date
            )
            self.account.record(date, self.broker.close_prices_at(date))
        return self.history_frame()

    def history_frame(self) -> pd.DataFrame:
        """Convert ``Account.history`` to a tabular equity and position history."""
        if not self.account.history:
            raise RuntimeError("run the engine before reading history")
        rows = []
        for snapshot in self.account.history:
            row: dict[str, float | int] = {
                "cash": snapshot.cash,
                "portfolio_value": snapshot.portfolio_value,
            }
            row.update(
                {f"position_{symbol}": shares for symbol, shares in snapshot.positions.items()}
            )
            row.update(
                {f"close_{symbol}": price for symbol, price in snapshot.close_prices.items()}
            )
            rows.append(row)
        dates = [snapshot.date for snapshot in self.account.history]
        return pd.DataFrame(rows, index=dates).rename_axis("Date")

    def daily_returns(self) -> pd.Series:
        """Return daily returns, including the first close versus initial cash."""
        values = self._portfolio_values()
        returns = values.pct_change()
        returns.iloc[0] = values.iloc[0] / self.account.initial_cash - 1
        return returns.rename("daily_return")

    def cumulative_return(self) -> float:
        """Portfolio return relative to the account's initial cash."""
        return float(self._portfolio_values().iloc[-1] / self.account.initial_cash - 1)

    def annualized_volatility(self) -> float:
        """Sample standard deviation of daily returns, annualized by sqrt(N)."""
        returns = self.daily_returns()
        if len(returns) < 2:
            return float("nan")
        return float(returns.std(ddof=1) * np.sqrt(self.annual_trading_days))

    def drawdown(self) -> pd.Series:
        """Return drawdowns using initial cash as the first high-water mark."""
        values = self._portfolio_values()
        peak = pd.Series(
            np.maximum.accumulate(np.r_[self.account.initial_cash, values.to_numpy()])[1:],
            index=values.index,
        )
        return (values / peak - 1).rename("drawdown")

    def max_drawdown(self) -> float:
        """Return maximum drawdown as a non-negative magnitude."""
        return float(-self.drawdown().min())

    def statistics(self) -> pd.Series:
        """Return the core statistics requested by the learning notebooks."""
        return pd.Series(
            {
                "cumulative_return": self.cumulative_return(),
                "annualized_volatility": self.annualized_volatility(),
                "max_drawdown": self.max_drawdown(),
            },
            name="value",
        )

    def _portfolio_values(self) -> pd.Series:
        if not self.account.history:
            raise RuntimeError("run the engine before calculating statistics")
        return pd.Series(
            [snapshot.portfolio_value for snapshot in self.account.history],
            index=[snapshot.date for snapshot in self.account.history],
            name="portfolio_value",
            dtype=float,
        )
