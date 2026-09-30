"""Observed-price order lifecycle and fee accounting tests."""
import unittest
import pandas as pd
from backtest import Account, SimBroker, DataFrameDataSource, Order, Engine, Strategy
from backtest.allocation import PeriodicAllocationStrategy


class PendingTests(unittest.TestCase):
    def setup_market(self, prices, cash=10000):
        self.dates = pd.bdate_range("2024-01-01", periods=len(prices))
        frame = pd.DataFrame({"Open": prices, "Close": prices}, index=self.dates)
        self.b = SimBroker(DataFrameDataSource({"A": frame}), self.dates[0], self.dates[-1], ["A"])
        self.a = Account(cash, ["A"])

    def submit(self, order, day=0):
        return self.b.execute_orders(self.a, [order], self.dates[day])[0]

    def advance(self, day):
        return self.b.execute_orders(self.a, [], self.dates[day])[0]

    def test_fees_both_sides_and_rejection(self):
        self.setup_market([100, 100], 20000)
        buy = self.submit(Order("A", "BUY", 100))
        self.assertEqual(buy.trade.commission, 10)
        self.assertEqual(self.a.cash, 9990)
        sell = self.submit(Order("A", "SELL", 1), 1)
        self.assertEqual(sell.trade.commission, 5)
        self.assertEqual(self.a.cash, 10085)
        self.setup_market([100], 100)
        self.assertEqual(self.submit(Order("A", "BUY", 1)).status, "REJECTED")
        self.assertEqual(self.a.cash, 100)
        self.assertEqual(self.a.positions["A"], 0)

    def test_limit_buy_gap_improvement_and_sell(self):
        self.setup_market([100, 88, 110])
        first = self.submit(Order("A", "BUY", 10, "LIMIT", limit_price=90))
        self.assertEqual(first.status, "PENDING")
        fill = self.advance(1)
        self.assertEqual(fill.trade.price, 88)
        self.assertEqual(fill.order_id, first.order_id)
        self.assertEqual(self.submit(Order("A", "SELL", 10, "LIMIT", limit_price=105), 1).status, "PENDING")
        self.assertEqual(self.advance(2).trade.price, 110)
        self.assertEqual(len(self.b.pending_orders), 0)

    def test_stop_sell_gaps_and_buy_stop(self):
        self.setup_market([100, 80])
        self.submit(Order("A", "BUY", 10))
        self.assertEqual(self.submit(Order("A", "SELL", 10, "STOP", stop_price=90)).status, "PENDING")
        self.assertEqual(self.advance(1).trade.price, 80)
        self.setup_market([100, 120])
        self.submit(Order("A", "BUY", 1, "STOP", stop_price=110))
        self.assertEqual(self.advance(1).trade.price, 120)

    def test_stop_limit_latches_and_respects_limit_both_sides(self):
        self.setup_market([100, 80, 96])
        self.submit(Order("A", "BUY", 10))
        self.submit(Order("A", "SELL", 10, "STOP_LIMIT", limit_price=92, stop_price=95))
        self.assertEqual(self.advance(1).status, "TRIGGERED")
        self.assertEqual(self.a.positions["A"], 10)
        # Rebounded above stop, but previously triggered order remains a limit.
        self.assertEqual(self.advance(2).trade.price, 96)
        self.setup_market([100, 120, 104])
        self.submit(Order("A", "BUY", 1, "STOP_LIMIT", limit_price=108, stop_price=110))
        self.assertEqual(self.advance(1).status, "TRIGGERED")
        self.assertEqual(self.advance(2).trade.price, 104)

    def test_touch_and_immediate_stop_limit(self):
        self.setup_market([100, 105])
        self.assertEqual(self.submit(Order("A", "BUY", 1, "LIMIT", limit_price=100)).status, "FILLED")
        self.submit(Order("A", "BUY", 1, "STOP_LIMIT", limit_price=105, stop_price=105))
        self.assertEqual(self.advance(1).trade.price, 105)

    def test_cancel_reset_and_no_reexecution(self):
        self.setup_market([100, 90])
        result = self.submit(Order("A", "BUY", 1, "LIMIT", limit_price=90))
        self.assertEqual(self.b.cancel_order(result.order_id).status, "CANCELED")
        self.assertEqual(self.b.execute_orders(self.a, [], self.dates[1]), [])
        with self.assertRaises(KeyError):
            self.b.cancel_order(result.order_id)
        self.b.reset()
        self.submit(Order("A", "BUY", 1, "LIMIT", limit_price=90))
        self.advance(1)
        self.assertEqual(self.b.execute_orders(self.a, [], self.dates[1]), [])
        self.assertEqual(self.a.positions["A"], 1)
        with self.assertRaises(ValueError):
            self.b.execute_orders(self.a, [], self.dates[0])

    def test_competing_orders_no_reservation_and_no_shorting(self):
        self.setup_market([100, 90], 100)
        self.submit(Order("A", "BUY", 1, "LIMIT", limit_price=90))
        self.submit(Order("A", "BUY", 1, "LIMIT", limit_price=90))
        results = self.b.execute_orders(self.a, [], self.dates[1])
        self.assertEqual([r.status for r in results], ["FILLED", "REJECTED"])
        self.assertEqual(self.a.cash, 5)
        self.assertEqual(self.submit(Order("A", "SELL", 2), 1).status, "REJECTED")

    def test_engine_advances_pending_before_strategy(self):
        self.setup_market([100, 90])
        class Once(Strategy):
            def reset(s): s.views = []
            def generate_orders(s, account, market, date):
                s.views.append(account.positions["A"])
                return [Order("A", "BUY", 1, "LIMIT", limit_price=90)] if len(s.views)==1 else []
        strategy = Once()
        engine = Engine(self.a, self.b, strategy)
        history = engine.run()
        self.assertEqual(strategy.views, [0, 1])
        self.assertEqual(history.portfolio_value.iloc[-1], 9995)
        self.assertEqual(engine.daily_order_results[self.dates[1]][0].status, "FILLED")
        pd.testing.assert_frame_equal(history, engine.run())

    def test_allocation_reserves_commission(self):
        self.setup_market([100, 110], 1000)
        self.b.execution_price = "close"
        engine = Engine(self.a, self.b, PeriodicAllocationStrategy("equal", ["A"], interval=1),
                        signal_timing="same_close")
        history = engine.run()
        self.assertEqual(history.position_A.iloc[0], 9)
        self.assertEqual(history.cash.iloc[0], 95)
        self.assertTrue(all(r.status == "FILLED" for r in self.b.order_history))

    def test_validation(self):
        for kind, kwargs in [("LIMIT", {}), ("STOP", {}), ("STOP_LIMIT", {"stop_price": 10}),
                             ("LIMIT", {"limit_price": float("nan")}), ("MARKET", {"limit_price": 10})]:
            with self.assertRaises(ValueError):
                Order("A", "BUY", 1, kind, **kwargs)


if __name__ == "__main__":
    unittest.main()
