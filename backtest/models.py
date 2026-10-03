"""Immutable values exchanged by accounts, strategies and brokers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Literal, Mapping, TypeVar

import pandas as pd

Side = Literal["BUY", "SELL"]
OrderStatus = Literal["PENDING", "TRIGGERED", "FILLED", "REJECTED", "CANCELED"]
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

    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT"] = "MARKET"
    limit_price: float | None = None
    stop_price: float | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("order symbol cannot be empty")
        if self.side not in ("BUY", "SELL"):
            raise ValueError("order side must be BUY or SELL")
        if isinstance(self.shares, bool) or not isinstance(self.shares, int):
            raise TypeError("order shares must be an integer")
        if self.shares <= 0:
            raise ValueError("order shares must be positive")
        if self.order_type not in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
            raise ValueError("unknown order_type")
        for name, required in (("limit_price", self.order_type in {"LIMIT", "STOP_LIMIT"}),
                               ("stop_price", self.order_type in {"STOP", "STOP_LIMIT"})):
            value = getattr(self, name)
            if required:
                if value is None or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be finite and positive")
            elif value is not None:
                raise ValueError(f"{name} is not used by {self.order_type}")


@dataclass(frozen=True, slots=True)
class Trade:
    """A filled order at the configured execution price."""

    date: pd.Timestamp
    symbol: str
    side: Side
    shares: int
    price: float
    cash_after: float
    commission: float = 0.0
    order_id: int | None = None
    stamp_tax: float = 0.0
    slippage: float = 0.0
    market_price: float | None = None

    @property
    def fees(self):
        """Cash fees only: slippage is already included in price."""
        return self.commission + self.stamp_tax


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Broker response for one submitted order."""

    date: pd.Timestamp
    order: Order
    status: OrderStatus
    reason: str | None = None
    trade: Trade | None = None
    order_id: int | None = None


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
    """Price history cut off at the engine's configured decision time."""

    open: pd.DataFrame
    close: pd.DataFrame
