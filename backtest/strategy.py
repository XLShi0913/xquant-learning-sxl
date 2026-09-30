"""The strategy contract; concrete trading logic belongs in experiments."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import pandas as pd

from .models import AccountView, MarketHistory, Order, OrderResult


class Strategy(ABC):
    """Create today's orders without mutating account or broker state."""

    def reset(self) -> None:
        """Reset optional strategy state before a new engine run."""

    def configure_execution(self, broker) -> None:
        """Optional fee-model binding before a run; existing strategies need no changes."""

    def after_execution(
        self, account: AccountView, market: MarketHistory,
        date: pd.Timestamp, results: Sequence[OrderResult],
    ) -> Sequence[Order]:
        """Optional one-pass hook for orders based on actual fills (e.g. protection).

        Account is read-only. Implementations may cancel their own pending orders
        through a bound broker. Returned orders are matched once at this sample;
        the engine does not recursively invoke this hook.
        """
        return []

    @abstractmethod
    def generate_orders(
        self, account: AccountView, market: MarketHistory, date: pd.Timestamp,
    ) -> Sequence[Order]:
        """Return orders using supplied history (exclusive by default).

        Engine's explicit same_close research mode includes the current bar.
        """
