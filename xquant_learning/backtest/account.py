"""Cash, positions and end-of-day account history."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from .models import AccountSnapshot, AccountView, readonly_mapping


class Account:
    """A long-only cash account with an end-of-day snapshot list."""

    def __init__(self, initial_cash: float, symbols: Iterable[str]) -> None:
        initial_cash = float(initial_cash)
        if not np.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        unique_symbols = tuple(dict.fromkeys(symbols))
        if not unique_symbols or any(not symbol for symbol in unique_symbols):
            raise ValueError("symbols must contain at least one non-empty id")
        self.initial_cash = initial_cash
        self.symbols = unique_symbols
        self.reset()

    def reset(self) -> None:
        """Restore initial cash and clear positions and history."""
        self.cash = self.initial_cash
        self.positions = {symbol: 0 for symbol in self.symbols}
        self.history: list[AccountSnapshot] = []

    def view(self) -> AccountView:
        """Return a defensive view suitable for strategy input."""
        return AccountView(cash=self.cash, positions=readonly_mapping(self.positions))

    def record(self, date: pd.Timestamp, close_prices: Mapping[str, float]) -> AccountSnapshot:
        """Append one end-of-day mark-to-market snapshot."""
        missing = set(self.symbols).difference(close_prices)
        if missing:
            raise ValueError(f"missing close prices for: {sorted(missing)}")
        prices = {symbol: float(close_prices[symbol]) for symbol in self.symbols}
        if any(not np.isfinite(price) or price <= 0 for price in prices.values()):
            raise ValueError("close prices must be finite and positive")
        value = self.cash + sum(self.positions[symbol] * prices[symbol] for symbol in self.symbols)
        snapshot = AccountSnapshot(
            date=pd.Timestamp(date), cash=self.cash,
            positions=readonly_mapping(self.positions), close_prices=readonly_mapping(prices),
            portfolio_value=value,
        )
        self.history.append(snapshot)
        return snapshot

    def _buy(self, symbol: str, shares: int, price: float) -> None:
        self.cash -= shares * price
        self.positions[symbol] += shares

    def _sell(self, symbol: str, shares: int, price: float) -> None:
        self.cash += shares * price
        self.positions[symbol] -= shares
