"""Allocation and close-execution accounting regression tests."""
import unittest
import numpy as np
import pandas as pd
from backtest import Account, SimBroker, Engine, DataFrameDataSource
from backtest.allocation import (
    PeriodicAllocationStrategy, BuyAndHoldEqualWeightStrategy, allocation_weights,
)


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

    def test_separate_windows_match_formula_and_legacy_defaults(self):
        returns = np.array([[.01, .02, -.04], [.04, .01, -.02],
                            [.02, .05, -.03], [.05, .03, -.01]])
        prices = pd.DataFrame(100 * np.exp(np.vstack([np.zeros(3), returns.cumsum(axis=0)])))
        for method in ("ram", "risk_parity"):
            np.testing.assert_array_equal(allocation_weights(method, prices, 4),
                allocation_weights(method, prices, 4, momentum_window=4, volatility_window=4))
        sigma = returns.std(axis=0, ddof=1)
        momentum = returns[-2:].mean(axis=0)
        scores = np.maximum(momentum, 0) / sigma
        expected = np.r_[scores / scores.sum(), 0.]
        np.testing.assert_allclose(allocation_weights("ram", prices, 4,
            momentum_window=2, volatility_window=4), expected)
        np.testing.assert_allclose(allocation_weights("ram", prices.iloc[:4], 4,
            momentum_window=2, volatility_window=4), [0, 0, 0, 1])
        short_vol = returns[-2:].std(axis=0, ddof=1)
        scores = np.maximum(returns.mean(axis=0), 0) / short_vol
        np.testing.assert_allclose(allocation_weights("ram", prices, 4,
            momentum_window=4, volatility_window=2), np.r_[scores / scores.sum(), 0.])

    def test_risk_parity_ignores_momentum_even_during_warmup(self):
        prices = pd.DataFrame([[100, 100], [101, 103], [105, 104]])
        expected = allocation_weights("risk_parity", prices, 2)
        self.assertEqual(expected[-1], 0)
        for momentum in (2, 20, 100):
            np.testing.assert_array_equal(allocation_weights("risk_parity", prices, 2,
                momentum_window=momentum, volatility_window=2), expected)

    def test_invalid_separate_windows_are_rejected(self):
        for name in ("momentum_window", "volatility_window"):
            for value in (1, 2.5, True, np.nan):
                with self.assertRaises(ValueError):
                    PeriodicAllocationStrategy("ram", ["A"], **{name: value})

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

    def test_equal_buy_and_hold_only_trades_on_first_date(self):
        dates = pd.bdate_range("2021-01-04", periods=4)
        prices = {"A": [10, 20, 30, 40], "B": [10, 5, 8, 4], "C": [10, 10, 12, 10]}
        source = DataFrameDataSource({
            s: pd.DataFrame({"Open": values, "Close": values}, index=dates)
            for s, values in prices.items()
        })
        broker = SimBroker(source, dates[0], dates[-1], list(prices), execution_price="close")
        strategy = BuyAndHoldEqualWeightStrategy(list(prices))
        engine = Engine(Account(10000, list(prices)), broker, strategy, signal_timing="same_close")
        ledger = engine.run()
        self.assertEqual(len(broker.trades), 3)
        self.assertTrue(all(t.side == "BUY" and t.date == dates[0] for t in broker.trades))
        self.assertEqual(sum(t.commission for t in broker.trades), 15)
        self.assertEqual(len(strategy.audit), 1)
        self.assertTrue((ledger.cash == ledger.cash.iloc[0]).all())
        for symbol in prices:
            self.assertTrue((ledger[f"position_{symbol}"] == ledger[f"position_{symbol}"].iloc[0]).all())
        expected_value = ledger.cash.iloc[0] + sum(
            ledger[f"position_{symbol}"].iloc[0] * values[-1] for symbol, values in prices.items())
        self.assertAlmostEqual(ledger.portfolio_value.iloc[-1], expected_value)
        self.assertFalse(any(r.status == "REJECTED" for r in broker.order_history))
        pd.testing.assert_frame_equal(ledger, engine.run())


if __name__ == "__main__":
    unittest.main()
