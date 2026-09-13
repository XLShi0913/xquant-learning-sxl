"""The strategy contract; concrete trading logic belongs in experiments."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import pandas as pd

from .models import AccountView, MarketHistory, Order


class Strategy(ABC):
    """Create today's orders without mutating account or broker state."""

    def reset(self) -> None:
        """Reset optional strategy state before a new engine run."""

    @abstractmethod
    def generate_orders(
        self, account: AccountView, market: MarketHistory, date: pd.Timestamp,
    ) -> Sequence[Order]:
        """Return orders for ``date`` using only history before ``date``."""
