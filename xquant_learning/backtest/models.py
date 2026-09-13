"""Immutable values exchanged by accounts, strategies and brokers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, TypeVar

import pandas as pd

Side = Literal["BUY", "SELL"]
OrderStatus = Literal["FILLED", "REJECTED"]
ValueT = TypeVar("ValueT", int, float)


def readonly_mapping(values: Mapping[str, ValueT]) -> Mapping[str, ValueT]:
    """Return a defensive, read-only copy of a mapping."""
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class Order:
    """An instruction produced by a strategy."""

    symbol: str
    side: Side
    shares: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("order symbol cannot be empty")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("order side must be BUY or SELL")
        if isinstance(self.shares, bool) or not isinstance(self.shares, int):
            raise TypeError("order shares must be an integer")
        if self.shares <= 0:
            raise ValueError("order shares must be positive")


@dataclass(frozen=True, slots=True)
class Trade:
    """A filled order at one trading day's opening price."""

    date: pd.Timestamp
    symbol: str
    side: Side
    shares: int
    price: float
    cash_after: float


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Broker response for one submitted order."""

    date: pd.Timestamp
    order: Order
    status: OrderStatus
    reason: str | None = None
    trade: Trade | None = None


@dataclass(frozen=True, slots=True)
class AccountView:
    """Read-only account state supplied to a strategy."""

    cash: float
    positions: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """End-of-day account state retained in ``Account.history``."""

    date: pd.Timestamp
    cash: float
    positions: Mapping[str, int]
    close_prices: Mapping[str, float]
    portfolio_value: float


@dataclass(frozen=True, slots=True)
class MarketHistory:
    """Opening and closing prices strictly before the decision date."""

    open: pd.DataFrame
    close: pd.DataFrame
