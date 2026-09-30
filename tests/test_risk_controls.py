"""Protection is based on actual fills, and exits cannot immediately re-enter."""

import unittest

import numpy as np
import pandas as pd

from backtest import Account, DataFrameDataSource, Engine, SimBroker
from backtest.allocation import PeriodicAllocationStrategy
from backtest.risk_controls import ProtectedRiskParityStrategy


class RiskControlsTests(unittest.TestCase):
    def run_case(self, prices, stop=None, profit=None, interval=3):
        dates = pd.bdate_range("2024-01-01", periods=len(prices))
        frame = pd.DataFrame({"Open": prices, "Close": prices}, index=dates)
        broker = SimBroker(DataFrameDataSource({"A": frame}), dates[0], dates[-1],
                           ["A"], execution_price="close")
        strategy = ProtectedRiskParityStrategy(["A"], 2, interval, stop, profit)
        engine = Engine(Account(10000, ["A"]), broker, strategy, signal_timing="same_close")
        ledger = engine.run()
        self.assertFalse([e for e in broker.order_history if e.status == "REJECTED"])
        self.assertTrue((ledger.cash >= 0).all())
        self.assertTrue((ledger.position_A >= 0).all())
        return engine, ledger

    def test_stop_gap_and_reentry_not_on_exit_day(self):
        engine, ledger = self.run_case([100, 101, 100, 100, 102, 101, 90, 100, 101, 100], .05, .20)
        exits = engine.strategy.protective_exits
        self.assertEqual(exits[0]["kind"], "stop_loss")
        self.assertEqual(exits[0]["price"], 90)
        self.assertEqual(ledger.position_A.iloc[6], 0)
        self.assertEqual(ledger.position_A.iloc[8], 0)
        self.assertGreater(ledger.position_A.iloc[9], 0)
        self.assertTrue(any(e.status == "CANCELED" and e.order.order_type == "LIMIT"
                            for e in engine.broker.order_history))

    def test_profit_gap_and_sibling_cancellation(self):
        engine, ledger = self.run_case([100, 101, 100, 100, 102, 101, 120, 100, 101, 100], .05, .10)
        self.assertEqual(engine.strategy.protective_exits[0]["kind"], "take_profit")
        self.assertEqual(engine.strategy.protective_exits[0]["price"], 120)
        self.assertEqual(ledger.position_A.iloc[6], 0)
        self.assertGreater(ledger.position_A.iloc[9], 0)
        self.assertTrue(any(e.status == "CANCELED" and e.order.order_type == "STOP"
                            for e in engine.broker.order_history))

    def test_no_protection_matches_original_and_rerun(self):
        engine, ledger = self.run_case([100, 101, 100, 100, 102, 101, 105, 103, 104, 106])
        original = Engine(Account(10000, ["A"]), engine.broker,
                          PeriodicAllocationStrategy("risk_parity", ["A"], 2, 3),
                          signal_timing="same_close")
        pd.testing.assert_frame_equal(ledger, original.run())
        pd.testing.assert_frame_equal(ledger, engine.run())

    def test_average_cost_addition_reduction_and_clear(self):
        engine, _ = self.run_case([100, 101, 100, 100, 102, 101, 90, 100, 101, 100], .05)
        self.assertAlmostEqual(engine.strategy.average_price["A"], 100)
        events = engine.broker.order_history
        held, basis = 0, 0.0
        for event in events:
            if event.status != "FILLED":
                continue
            t = event.trade
            if t.side == "BUY":
                basis = (held * basis + t.shares * t.price) / (held + t.shares)
                held += t.shares
            else:
                held -= t.shares
                if held == 0:
                    basis = 0.0
        self.assertAlmostEqual(basis, engine.strategy.average_price["A"])
        self.assertEqual(held, engine.account.positions["A"])

    def test_invalid_thresholds(self):
        for value in (0, -.1, 1, np.nan, True):
            with self.assertRaises(ValueError):
                ProtectedRiskParityStrategy(["A"], stop_loss=value)


if __name__ == "__main__":
    unittest.main()
