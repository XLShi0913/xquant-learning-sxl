"""Order sizing, price slippage and cash fees independent of broker matching."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import math

from .configuration import resolve_config


class SlippageModel(ABC):
    @abstractmethod
    def execution_price(self, symbol, side, shares, market_price, *, date=None):
        """Return modeled price; future models may use symbol/quantity/date."""


@dataclass(frozen=True)
class FixedRateSlippage(SlippageModel):
    rate: float

    def __post_init__(self):
        if isinstance(self.rate, bool) or not math.isfinite(self.rate) or not 0 <= self.rate < 1:
            raise ValueError("slippage rate must be in [0, 1)")

    def execution_price(self, symbol, side, shares, market_price, *, date=None):
        return market_price * (1 + self.rate if side == "BUY" else 1 - self.rate)


@dataclass(frozen=True)
class CostQuote:
    price: float
    market_price: float
    amount: float
    commission: float
    stamp_tax: float
    slippage: float
    side: str

    @property
    def fees(self):
        return self.commission + self.stamp_tax

    @property
    def cash_delta(self):
        return (-self.amount if self.side == "BUY" else self.amount) - self.fees


class TransactionCostModel:
    def __init__(self, *, config=None, commission_rate=None, minimum_commission=None,
                 stamp_tax_rate=None, lot_size=None, slippage_rate=None,
                 instrument_types=None, slippage_model=None):
        settings = resolve_config(config).section("broker")
        for name, value in (("commission_rate", commission_rate),
                            ("minimum_commission", minimum_commission),
                            ("stamp_tax_rate", stamp_tax_rate), ("lot_size", lot_size),
                            ("slippage_rate", slippage_rate)):
            setattr(self, name, settings[name] if value is None else value)
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size < 1:
            raise ValueError("lot_size must be a positive integer")
        for name in ("commission_rate", "minimum_commission", "stamp_tax_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if self.commission_rate >= 1 or self.stamp_tax_rate >= 1:
            raise ValueError("fee rates must be less than one")
        self.instrument_types = settings["instrument_types"] if instrument_types is None else dict(instrument_types)
        if any(kind not in {"stock", "etf"} for kind in self.instrument_types.values()):
            raise ValueError("instrument types must be stock or etf")
        self.slippage_model = slippage_model or FixedRateSlippage(self.slippage_rate)

    def commission(self, amount):
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("amount must be finite and nonnegative")
        return max(amount * self.commission_rate, self.minimum_commission) if amount else 0.0

    def quote(self, symbol, side, shares, market_price, *, date=None, limit_price=None):
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0 or shares % self.lot_size:
            raise ValueError("shares must be a nonnegative lot_size multiple")
        if not math.isfinite(market_price) or market_price <= 0:
            raise ValueError("market price must be finite and positive")
        price = self.slippage_model.execution_price(symbol, side, shares, market_price, date=date)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("slippage model returned an invalid price")
        if limit_price is not None:
            if isinstance(limit_price, bool) or not math.isfinite(limit_price) or limit_price <= 0:
                raise ValueError("limit price must be finite and positive")
            price = min(price, limit_price) if side == "BUY" else max(price, limit_price)
        amount = shares * price
        # Explicitly mark ETF symbols; unlisted instruments use the stock rule.
        tax = amount * self.stamp_tax_rate if side == "SELL" and self.instrument_types.get(symbol, "stock") == "stock" else 0.0
        return CostQuote(price, market_price, amount, self.commission(amount), tax,
                         abs(price - market_price) * shares, side)
