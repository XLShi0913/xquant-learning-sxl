"""Allocation and close-execution accounting regression tests."""
import unittest
import numpy as np
import pandas as pd
from backtest import Account, SimBroker, Engine, DataFrameDataSource
from backtest.allocation import PeriodicAllocationStrategy, allocation_weights


class AllocationTests(unittest.TestCase):
    def test_warmup_and_positive_ram(self):
        # Asset 0/1 share return volatility but have different positive means;
        # asset 2 has a negative mean and must receive no allocation.
        returns = np.array([[.01, .03, -.04], [.03, .05, -.02]])
        prices = pd.DataFrame(100 * np.exp(np.vstack([np.zeros(3), returns.cumsum(axis=0)])))
        np.testing.assert_allclose(allocation_weights("ram", prices.iloc[:2], 2), [0, 0, 0, 1])
        np.testing.assert_allclose(allocation_weights("ram", prices, 2), [1/3, 2/3, 0, 0])
        np.testing.assert_allclose(allocation_weights("ram", 1/prices, 2), [0, 0, 1, 0])
        decreasing = pd.DataFrame(100*np.exp(np.vstack([np.zeros(3), -abs(returns).cumsum(axis=0)])))
        np.testing.assert_allclose(allocation_weights("ram", decreasing, 2), [0, 0, 0, 1])

    def test_integer_close_rebalancing_and_legacy_default(self):
        dates = pd.bdate_range("2021-01-04", periods=3)
        data = {
            "A": pd.DataFrame({"Open": [1, 1, 1], "Close": [10, 20, 20]}, index=dates),
            "B": pd.DataFrame({"Open": [1, 1, 1], "Close": [10, 10, 10]}, index=dates),
        }
        source = DataFrameDataSource(data)
        broker = SimBroker(source, dates[0], dates[-1], ["A", "B"], execution_price="close",
                           commission_rate=0, minimum_commission=0)
        engine = Engine(Account(105, ["A", "B"]), broker,
                        PeriodicAllocationStrategy("equal", ["A", "B"], interval=1),
                        signal_timing="same_close")
        ledger = engine.run()
        # Day 0: 5 shares each and 5 cash. Day 1: equity=155,
        # sell 2 A at 20 then buy 2 B at 10 -> 3 A, 7 B, 25 cash.
        self.assertEqual(ledger.iloc[1].portfolio_value, 155)
        self.assertEqual(ledger.iloc[1].cash, 25)
        self.assertEqual(ledger.iloc[1].position_A, 3)
        self.assertEqual(ledger.iloc[1].position_B, 7)
        self.assertTrue(all(r.status == "FILLED" for r in broker.order_history))
        pd.testing.assert_frame_equal(ledger, engine.run())
        default = SimBroker(source, dates[0], dates[-1], ["A", "B"])
        self.assertEqual(default.execution_price, "open")
        with self.assertRaises(ValueError):
            Engine(Account(105, ["A", "B"]), default, engine.strategy, signal_timing="same_close")


if __name__ == "__main__":
    unittest.main()
