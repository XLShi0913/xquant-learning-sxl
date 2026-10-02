"""Average-entry-price protection for periodic, same-close allocations."""

import math

from .allocation import PeriodicAllocationStrategy
from .models import Order


class ProtectedAllocationStrategy(PeriodicAllocationStrategy):
    """Fixed per-asset thresholds; re-entry only at subsequent rebalance dates.

    Average purchase price excludes commissions, which affect account equity.
    Partial reductions retain the average; additions update it; full exits clear
    it. No trailing thresholds or reference-price reset at each rebalance.
    Own STOP/LIMIT sells are refreshed after actual holdings change. Siblings
    are canceled before new strategy orders after a protective fill. This is a
    daily-price simulation, not an intraday or atomic exchange OCO mechanism.
    """

    def __init__(self, method, symbols, period=20, interval=10, stop_loss=None, take_profit=None, *,
                 momentum_window=None, volatility_window=None):
        for name, value in (("stop_loss", stop_loss), ("take_profit", take_profit)):
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value <= 0
                or (name == "stop_loss" and value >= 1)
            ):
                raise ValueError(f"invalid {name}")
        self.stop_loss, self.take_profit = stop_loss, take_profit
        super().__init__(method, symbols, period, interval,
                         momentum_window=momentum_window, volatility_window=volatility_window)

    def reset(self):
        super().reset()
        self.average_price = dict.fromkeys(self.symbols, 0.0)
        self._held = dict.fromkeys(self.symbols, 0)
        self._history_cursor = 0
        self._exit_date = {}
        self._refresh = False
        self._broker = None
        self.protective_exits = []

    def configure_execution(self, broker):
        super().configure_execution(broker)
        self._broker = broker

    def _sync_fills(self):
        events = self._broker.order_history[self._history_cursor:]
        self._history_cursor = len(self._broker.order_history)
        for event in events:
            if event.status != "FILLED":
                continue
            trade, symbol = event.trade, event.order.symbol
            if symbol not in self._held:
                continue
            old = self._held[symbol]
            if trade.side == "BUY":
                self.average_price[symbol] = (
                    old * self.average_price[symbol] + trade.shares * trade.price
                ) / (old + trade.shares)
                self._held[symbol] += trade.shares
            else:
                self._held[symbol] -= trade.shares
                if self._held[symbol] < 0:
                    raise RuntimeError("protective accounting produced a short position")
                if self._held[symbol] == 0:
                    self.average_price[symbol] = 0.0
                if event.order.order_type in {"STOP", "LIMIT"}:
                    self._exit_date[symbol] = trade.date
                    self.protective_exits.append({
                        "date": trade.date, "symbol": symbol,
                        "kind": "stop_loss" if event.order.order_type == "STOP" else "take_profit",
                        "shares": trade.shares, "price": trade.price,
                        "commission": trade.commission, "order_id": event.order_id,
                    })
            self._refresh = True

    def _cancel_owned_protection(self):
        for event in self._broker.pending_orders:
            order = event.order
            if order.symbol in self.symbols and order.side == "SELL" and order.order_type in {"STOP", "LIMIT"}:
                self._broker.cancel_order(event.order_id)

    def generate_orders(self, account, market, date):
        self._sync_fills()
        scheduled = self.bar_count % self.interval == 0
        if self._refresh or scheduled:
            self._cancel_owned_protection()
        orders = super().generate_orders(account, market, date)
        # Protective exits cannot be undone by a rebalance at the same sample.
        # The affected allocation stays in cash; other targets are unchanged.
        return [o for o in orders if not (
            o.side == "BUY" and self._exit_date.get(o.symbol) == date
        )]

    def after_execution(self, account, market, date, results):
        self._sync_fills()
        for symbol in self.symbols:
            if account.positions[symbol] != self._held[symbol]:
                raise RuntimeError("protective accounting does not match account")
        scheduled = (self.bar_count - 1) % self.interval == 0
        if not (self._refresh or scheduled):
            return []
        self._cancel_owned_protection()
        orders = []
        for symbol in self.symbols:
            shares = account.positions[symbol]
            if not shares:
                continue
            basis = self.average_price[symbol]
            if self.stop_loss is not None:
                orders.append(Order(symbol, "SELL", shares, order_type="STOP",
                                    stop_price=basis * (1 - self.stop_loss)))
            if self.take_profit is not None:
                orders.append(Order(symbol, "SELL", shares, order_type="LIMIT",
                                    limit_price=basis * (1 + self.take_profit)))
        self._refresh = False
        return orders


class ProtectedRiskParityStrategy(ProtectedAllocationStrategy):
    """Backward-compatible q4 constructor for protected risk parity."""

    def __init__(self, symbols, period=20, interval=10, stop_loss=None, take_profit=None, *,
                 momentum_window=None, volatility_window=None):
        super().__init__("risk_parity", symbols, period, interval, stop_loss, take_profit,
                         momentum_window=momentum_window, volatility_window=volatility_window)
