"""Periodic portfolio allocations for same-close research experiments."""

import numpy as np
import pandas as pd

from .models import Order
from .strategy import Strategy


def validate_weights(weights, tolerance=1e-6):
    """Validate the asset plus CASH simplex without silently repairing it."""
    w = np.asarray(weights, dtype=float)
    if w.ndim != 1 or not np.isfinite(w).all() or (w < 0).any():
        raise ValueError("weights must be finite and nonnegative")
    if abs(w.sum() - 1) > tolerance:
        raise ValueError("asset plus cash weights must sum to one")
    return w


def allocation_weights(method, closes, period=20):
    """Equal, diagonal risk parity, or positive RAM score weights plus CASH.

    Insufficient history (period+1 prices) means CASH for indicator strategies.
    Momentum is average log return; volatility is sample std of log returns.
    """
    n = closes.shape[1]
    if n == 0 or closes.empty:
        raise ValueError("close history is empty")
    if method == "equal":
        return validate_weights(np.r_[np.full(n, 1 / n), 0.0])
    if method not in {"risk_parity", "ram"}:
        raise ValueError("unknown allocation method")
    if len(closes) <= period:
        return validate_weights(np.r_[np.zeros(n), 1.0])
    log_prices = np.log(closes.tail(period + 1))
    sigma = log_prices.diff().iloc[1:].std(ddof=1).to_numpy()
    valid = np.isfinite(sigma) & (sigma > 0)
    scores = np.zeros(n)
    if method == "risk_parity":
        scores[valid] = 1 / sigma[valid]
    else:
        momentum = ((log_prices.iloc[-1] - log_prices.iloc[0]) / period).to_numpy()
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

    def __init__(self, method, symbols, period=20, interval=10):
        if method not in {"equal", "risk_parity", "ram"}:
            raise ValueError("unknown allocation method")
        if isinstance(period, bool) or not isinstance(period, int) or period < 2:
            raise ValueError("period must be an integer >= 2")
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

    def generate_orders(self, account, market, date):
        if market.close.empty or market.close.index[-1] != date:
            raise ValueError("PeriodicAllocationStrategy requires same_close engine mode")
        number = self.bar_count
        self.bar_count += 1
        if number % self.interval:
            return []
        history = market.close.loc[:, list(self.symbols)]
        w = allocation_weights(self.method, history, self.period)
        prices = history.iloc[-1].to_numpy(dtype=float)
        held = np.array([account.positions[s] for s in self.symbols], dtype=int)
        equity = float(account.cash + held @ prices)
        target = np.floor(equity * w[:-1] / prices).astype(int)
        delta = target - held
        orders = [Order(s, "SELL", int(-d)) for s, d in zip(self.symbols, delta) if d < 0]
        orders += [Order(s, "BUY", int(d)) for s, d in zip(self.symbols, delta) if d > 0]
        self.audit.append({"date": date, "bar": number,
                           "signal_date": history.index[-1], "observations": len(history),
                           **dict(zip((*self.symbols, "CASH"), w))})
        return orders
