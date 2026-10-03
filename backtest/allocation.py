"""Periodic portfolio allocations for same-close research experiments."""

import numpy as np
import pandas as pd

from .models import Order
from .strategy import Strategy
from .configuration import resolve_config


def validate_weights(weights, tolerance=1e-6):
    """Validate the asset plus CASH simplex without silently repairing it."""
    w = np.asarray(weights, dtype=float)
    if w.ndim != 1 or not np.isfinite(w).all() or (w < 0).any():
        raise ValueError("weights must be finite and nonnegative")
    if abs(w.sum() - 1) > tolerance:
        raise ValueError("asset plus cash weights must sum to one")
    return w


def _indicator_windows(period, momentum_window, volatility_window):
    """Resolve separate windows while keeping legacy period as both defaults."""
    momentum = period if momentum_window is None else momentum_window
    volatility = period if volatility_window is None else volatility_window
    for name, value in (("period", period), ("momentum_window", momentum),
                        ("volatility_window", volatility)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError(f"{name} must be an integer >= 2")
    return momentum, volatility


def allocation_weights(method, closes, period=None, *, momentum_window=None,
                       volatility_window=None, config=None):
    """Equal, diagonal risk parity, or positive RAM score weights plus CASH.

    Legacy period sets both windows unless separately overridden. Risk parity
    uses only volatility_window; RAM requires both windows plus one price.
    Insufficient history means CASH for indicator strategies.
    Momentum is average log return; volatility is sample std of log returns.
    """
    n = closes.shape[1]
    if n == 0 or closes.empty:
        raise ValueError("close history is empty")
    if method == "equal":
        return validate_weights(np.r_[np.full(n, 1 / n), 0.0])
    if method not in {"risk_parity", "ram"}:
        raise ValueError("unknown allocation method")
    if period is None:
        settings = resolve_config(config, legacy=True).section("strategy")
        momentum_window = settings["momentum_window"] if momentum_window is None else momentum_window
        volatility_window = settings["volatility_window"] if volatility_window is None else volatility_window
        period = momentum_window
    momentum_window, volatility_window = _indicator_windows(
        period, momentum_window, volatility_window)
    required = volatility_window if method == "risk_parity" else max(momentum_window, volatility_window)
    if len(closes) <= required:
        return validate_weights(np.r_[np.zeros(n), 1.0])
    volatility_prices = np.log(closes.tail(volatility_window + 1))
    sigma = volatility_prices.diff().iloc[1:].std(ddof=1).to_numpy()
    valid = np.isfinite(sigma) & (sigma > 0)
    scores = np.zeros(n)
    if method == "risk_parity":
        scores[valid] = 1 / sigma[valid]
    else:
        momentum_prices = np.log(closes.tail(momentum_window + 1))
        momentum = ((momentum_prices.iloc[-1] - momentum_prices.iloc[0]) / momentum_window).to_numpy()
        valid &= np.isfinite(momentum) & (momentum > 0)
        scores[valid] = momentum[valid] / sigma[valid]
    if scores.sum() == 0:
        return validate_weights(np.r_[np.zeros(n), 1.0])
    return validate_weights(np.r_[scores / scores.sum(), 0.0])


class PeriodicAllocationStrategy(Strategy):
    """Every interval bars, convert target weights to integer-share orders.

    Requires current close in market history and close execution. The first
    bar anchors the schedule, even when warmup forces the target to CASH.
    Sell orders precede buys. No share rounding is hidden by normalization.
    """

    def __init__(self, method=None, symbols=None, period=None, interval=None, *,
                 momentum_window=None, volatility_window=None, config=None):
        settings = resolve_config(config, legacy=True)
        strategy_settings = settings.section("strategy")
        method = strategy_settings["method"] if method is None else method
        symbols = settings.section("market")["symbols"] if symbols is None else symbols
        interval = strategy_settings["interval"] if interval is None else interval
        momentum_window = (period if period is not None else strategy_settings["momentum_window"]) if momentum_window is None else momentum_window
        volatility_window = (period if period is not None else strategy_settings["volatility_window"]) if volatility_window is None else volatility_window
        period = momentum_window if period is None else period
        if method not in {"equal", "risk_parity", "ram"}:
            raise ValueError("unknown allocation method")
        self.momentum_window, self.volatility_window = _indicator_windows(
            period, momentum_window, volatility_window)
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise ValueError("interval must be a positive integer")
        self.method, self.symbols = method, tuple(symbols)
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be nonempty and unique")
        self.period, self.interval = period, interval
        self.reset()

    def reset(self):
        self.bar_count = 0
        self.audit = []
        self._fee = lambda amount: 0.0
        self._quote = None
        self._lot_size = None

    def configure_execution(self, broker):
        self._fee = broker.estimate_commission
        self._quote = broker.quote_order
        self._lot_size = broker.lot_size

    def generate_orders(self, account, market, date):
        if market.close.empty or market.close.index[-1] != date:
            raise ValueError("PeriodicAllocationStrategy requires same_close engine mode")
        number = self.bar_count
        self.bar_count += 1
        if number % self.interval:
            return []
        history = market.close.loc[:, list(self.symbols)]
        w = allocation_weights(self.method, history, self.period,
                               momentum_window=self.momentum_window,
                               volatility_window=self.volatility_window)
        prices = history.iloc[-1].to_numpy(dtype=float)
        held = np.array([account.positions[s] for s in self.symbols], dtype=int)
        equity = float(account.cash + held @ prices)
        if self._quote is None:
            raise RuntimeError("bind broker through configure_execution before generating orders")
        lot = self._lot_size
        target = np.floor(equity * w[:-1] / prices / lot).astype(int) * lot
        delta = target - held
        orders = [Order(s, "SELL", int(-d)) for s, d in zip(self.symbols, delta) if d < 0]
        available = account.cash + sum(
            self._quote(s, "SELL", int(-d), p, date=date).cash_delta
            for s, d, p in zip(self.symbols, delta, prices) if d < 0)
        # Full target quantities may exceed cash once commissions are included.
        # Keep sell-first ordering, then cap each buy to an affordable integer.
        for s, d, price in zip(self.symbols, delta, prices):
            if d <= 0:
                continue
            low, high = 0, int(d) // lot
            while low < high:
                mid = (low + high + 1) // 2
                if -self._quote(s, "BUY", mid * lot, price, date=date).cash_delta <= available + 1e-12:
                    low = mid
                else:
                    high = mid - 1
            if low:
                quantity = low * lot
                orders.append(Order(s, "BUY", quantity))
                available += self._quote(s, "BUY", quantity, price, date=date).cash_delta
        self.audit.append({"date": date, "bar": number,
                           "signal_date": history.index[-1], "observations": len(history),
                           **dict(zip((*self.symbols, "CASH"), w))})
        return orders


class BuyAndHoldEqualWeightStrategy(PeriodicAllocationStrategy):
    """Allocate equally once at the first close, then never rebalance or sell.

    Reuse equal-allocation integer sizing and the broker's fee budget. Actual
    initial weights can deviate slightly due to shares/fees; subsequent weights
    drift with prices. Residual cash remains cash. Same-close engine required.
    """

    def __init__(self, symbols=None, *, config=None):
        super().__init__("equal", symbols, interval=1, config=config)

    def generate_orders(self, account, market, date):
        if self.bar_count:
            return []
        return super().generate_orders(account, market, date)
