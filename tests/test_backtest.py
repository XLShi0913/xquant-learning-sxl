"""Tests for the shared account, broker, strategy and engine modules."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from xquant_learning.backtest import (
    Account, DataFrameDataSource, Engine, Order, SimBroker, Strategy,
)


class BuyOneShare(Strategy):
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.observed_history_ends: list[pd.Timestamp | None] = []

    def generate_orders(self, account, market, date):
        history_end = market.close.index.max() if not market.close.empty else None
        self.observed_history_ends.append(history_end)
        if account.positions[self.symbol] == 0:
            return [Order(self.symbol, "BUY", 1)]
        return []


def price_frame(open_values, close_values) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(open_values))
    return pd.DataFrame({"Open": open_values, "Close": close_values}, index=dates)


class BacktestTests(unittest.TestCase):
    def make_engine(self, frame: pd.DataFrame, initial_cash: float = 100.0):
        symbol = "TEST"
        source = DataFrameDataSource({symbol: frame})
        broker = SimBroker(source, frame.index.min(), frame.index.max(), [symbol])
        account = Account(initial_cash, [symbol])
        strategy = BuyOneShare(symbol)
        return Engine(account, broker, strategy), strategy

    def test_strategy_is_an_interface(self) -> None:
        with self.assertRaises(TypeError):
            Strategy()  # type: ignore[abstract]

    def test_strategy_never_receives_current_or_future_prices(self) -> None:
        frame = price_frame([100, 105, 110], [101, 106, 111])
        engine, strategy = self.make_engine(frame, initial_cash=1000)
        engine.run()
        self.assertIsNone(strategy.observed_history_ends[0])
        pairs = zip(engine.broker.trading_dates[1:], strategy.observed_history_ends[1:])
        for decision_date, history_end in pairs:
            self.assertLess(history_end, decision_date)

    def test_order_fills_at_open_and_account_records_each_close(self) -> None:
        frame = price_frame([100, 105, 110], [101, 106, 111])
        engine, _ = self.make_engine(frame, initial_cash=1000)
        history = engine.run()
        self.assertEqual(len(engine.account.history), 3)
        self.assertEqual(engine.broker.trades[0].price, 100)
        self.assertEqual(history.iloc[0]["cash"], 900)
        self.assertEqual(history.iloc[0]["portfolio_value"], 1001)
        self.assertEqual(dict(engine.account.history[0].positions), {"TEST": 1})
        first_snapshot = engine.account.history[0]
        engine.account.positions["TEST"] = 7
        self.assertEqual(first_snapshot.positions["TEST"], 1)
        with self.assertRaises(TypeError):
            first_snapshot.positions["TEST"] = 2  # type: ignore[index]

    def test_rejected_order_does_not_change_account(self) -> None:
        frame = price_frame([100], [100])
        engine, _ = self.make_engine(frame, initial_cash=50)
        history = engine.run()
        result = engine.broker.order_history[0]
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.reason, "insufficient cash")
        self.assertEqual(history.iloc[0]["portfolio_value"], 50)

    def test_statistics_follow_account_history(self) -> None:
        frame = price_frame([100, 110, 90, 99], [100, 110, 90, 99])
        engine, _ = self.make_engine(frame, initial_cash=100)
        engine.run()
        expected_returns = pd.Series([0.0, 0.10, 90 / 110 - 1, 0.10])
        self.assertAlmostEqual(engine.cumulative_return(), -0.01)
        self.assertAlmostEqual(engine.max_drawdown(), 1 - 90 / 110)
        self.assertAlmostEqual(
            engine.annualized_volatility(),
            expected_returns.std(ddof=1) * np.sqrt(252),
        )

    def test_engine_can_be_rerun_without_duplicating_history(self) -> None:
        frame = price_frame([100, 101], [100, 101])
        engine, _ = self.make_engine(frame)
        first = engine.run()
        second = engine.run()
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(engine.account.history), 2)


if __name__ == "__main__":
    unittest.main()
