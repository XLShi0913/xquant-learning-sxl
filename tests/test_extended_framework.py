"""Offline tests: fees/lot sizing, YAML precedence, bounded data and scans."""
import unittest

import numpy as np
import pandas as pd

from backtest import (Account, DataFrameDataSource, SimBroker, Order, load_config,
                      TransactionCostModel, SlippageModel, run_backtest,
                      scan_parameter, scan_tied_windows, scan_grid, scan_metrics, select_best)


def source(n=90):
    dates = pd.bdate_range("2024-01-02", periods=n)
    return DataFrameDataSource({s: pd.DataFrame({
        "Open": 10 * np.exp(np.arange(n) * rate + np.sin(np.arange(n)) * .02),
        "Close": 10 * np.exp(np.arange(n) * rate + np.sin(np.arange(n)) * .02)}, index=dates)
        for s, rate in (("A", .001), ("B", .002))})


class ExtendedTests(unittest.TestCase):
    def cfg(self):
        return load_config({"market": {"start": "2024-01-02", "end": "2024-05-06", "symbols": ["A", "B"]},
                            "strategy": {"momentum_window": 10, "volatility_window": 10},
                            "broker": {"instrument_types": {"A": "stock", "B": "etf"}}})

    def test_costs_buy_sell_and_etf_exemption(self):
        model = TransactionCostModel(instrument_types={"ETF": "etf"})
        buy = model.quote("STOCK", "BUY", 100, 10)
        sell = model.quote("STOCK", "SELL", 100, 10)
        self.assertAlmostEqual(buy.price, 10.01)
        self.assertAlmostEqual(buy.cash_delta, -1006)
        self.assertAlmostEqual(sell.price, 9.99)
        self.assertAlmostEqual(sell.stamp_tax, 999 * .0005)
        self.assertEqual(sell.commission, 5)
        self.assertAlmostEqual(sell.cash_delta, 999 - 5 - .4995)
        self.assertEqual(model.quote("ETF", "SELL", 100, 10).stamp_tax, 0)
        self.assertAlmostEqual(model.quote("STOCK", "BUY", 10000, 100).commission, 200.2)

    def test_lot_is_hard_constraint_both_sides_and_pending(self):
        cfg = self.cfg()
        broker = SimBroker(source(), "2024-01-02", "2024-05-06", ["A", "B"], config=cfg)
        account = Account(symbols=["A", "B"], config=cfg)
        date = broker.trading_dates[0]
        for side in ["BUY", "SELL"]:
            result = broker.execute_orders(account, [Order("A", side, 99)], date)[0]
            self.assertEqual(result.status, "REJECTED")
        result = broker.execute_orders(account, [Order("A", "BUY", 99, order_type="LIMIT", limit_price=1)], date)[0]
        self.assertEqual(result.status, "REJECTED")
        self.assertFalse(broker.pending_orders)
        with self.assertRaises(ValueError):
            broker.quote_order("A", "BUY", 99, 10)

    def test_limit_caps_slippage_and_custom_interface(self):
        model = TransactionCostModel()
        self.assertEqual(model.quote("A", "BUY", 100, 10, limit_price=10).price, 10)
        self.assertEqual(model.quote("A", "SELL", 100, 10, limit_price=10).price, 10)
        class Custom(SlippageModel):
            def execution_price(self, symbol, side, shares, market_price, *, date=None):
                return market_price + (.05 if side == "BUY" else -.05)
        model = TransactionCostModel(slippage_model=Custom())
        self.assertEqual(model.quote("A", "BUY", 100, 10).price, 10.05)

    def test_limit_and_stop_limit_real_fills_never_violate_limit(self):
        cfg = self.cfg()
        sim = SimBroker(source(), "2024-01-02", "2024-05-06", ["A", "B"], config=cfg)
        account = Account(symbols=["A", "B"], config=cfg)
        date = sim.trading_dates[0]
        for kind, extra in [("LIMIT", {}), ("STOP_LIMIT", {"stop_price": 9})]:
            fill = sim.execute_orders(account, [Order("A", "BUY", 100, order_type=kind,
                                      limit_price=10, **extra)], date)[0]
            self.assertEqual(fill.status, "FILLED")
            self.assertLessEqual(fill.trade.price, 10)

    def test_cash_reconciliation_and_round_lots(self):
        result = run_backtest(source(), config=self.cfg(), method="ram", stop_loss=.05)
        positions = result.ledger[["position_A", "position_B"]]
        self.assertTrue((positions.to_numpy() % 100 == 0).all())
        cash = result.engine.account.initial_cash
        for trade in result.engine.broker.trades:
            cash += (-1 if trade.side == "BUY" else 1) * trade.shares * trade.price - trade.fees
            self.assertAlmostEqual(cash, trade.cash_after, places=6)
        self.assertTrue((result.ledger.cash >= 0).all())

    def test_config_priority_zero_and_explicit_none(self):
        cfg = self.cfg().override(strategy={"stop_loss": .05}, broker={"commission_rate": .1})
        result = run_backtest(source(), config=cfg, stop_loss=None, momentum_window=15,
                              broker_parameters={"commission_rate": 0, "minimum_commission": 0})
        self.assertEqual(result.engine.strategy.momentum_window, 15)
        self.assertEqual(result.engine.broker.commission_rate, 0)
        self.assertFalse(hasattr(result.engine.strategy, "stop_loss"))
        self.assertEqual(sum(t.commission for t in result.engine.broker.trades), 0)
        raw = cfg.section("broker")
        raw["lot_size"] = 1
        self.assertEqual(cfg.section("broker")["lot_size"], 100)
        for patch in [{"strategy": {"typo": 10}}, {"broker": None}]:
            with self.assertRaises(ValueError):
                load_config(patch)

    def test_data_split_blocks_future_and_pre_start(self):
        data = source()
        split = data.split(train_start="2024-01-02", train_end="2024-02-01",
                           validation_start="2024-02-02", validation_end="2024-05-06")
        train = split.train.get_bars("A", "2020-01-01", "2030-01-01")
        valid = split.validation.get_bars("A", "2020-01-01", "2030-01-01")
        self.assertLess(train.index.max(), valid.index.min())
        self.assertGreaterEqual(valid.index.min(), pd.Timestamp("2024-02-02"))
        result = run_backtest(split.validation, config=self.cfg())
        audit = result.engine.strategy.audit
        self.assertEqual(audit[0]["observations"], 1)
        with self.assertRaises(ValueError):
            data.split(train_start="2024-01-02", train_end="2024-02-02",
                       validation_start="2024-02-02", validation_end="2024-05-06")

    def test_scan_factories_and_tied_windows(self):
        data, cfg = source(), self.cfg()
        runner = lambda **kw: run_backtest(data, config=cfg, **kw)
        results = scan_tied_windows(runner, [10, 15, 20], fixed={"method": "ram"})
        self.assertEqual(len({id(r.engine.account) for r in results.values()}), 3)
        for v, r in results.items():
            self.assertEqual(r.engine.strategy.momentum_window, v)
            self.assertEqual(r.engine.strategy.volatility_window, v)
        self.assertEqual(scan_metrics(results).shape, (3, 8))
        self.assertIn(select_best(results), results)
        expected = run_backtest(data, config=cfg, method="ram", momentum_window=10, volatility_window=10)
        pd.testing.assert_frame_equal(expected.ledger, results[10].ledger)
        self.assertEqual(len(scan_parameter(runner, "interval", [5, 10])), 2)
        self.assertEqual(len(scan_grid(runner, {"interval": [5, 10], "volatility_window": [10, 20]})), 4)
        self.assertEqual(len(scan_grid(runner, {"interval": iter([5, 10])})), 2)

    def test_analysis_config_priority(self):
        from backtest.performance import performance_metrics, drawdown_events
        equity = pd.Series([100, 99.9, 101, 100, 102], index=pd.bdate_range("2024-01-02", periods=5))
        cfg = self.cfg().override(engine={"annual_trading_days": 250}, analysis={"mar_annual": .03, "drawdown_threshold": -.05})
        actual = performance_metrics(equity, 100, config=cfg)
        explicit = performance_metrics(equity, 100, annual_trading_days=250, mar_annual=.03)
        pd.testing.assert_series_equal(actual, explicit)
        zero = performance_metrics(equity, 100, config=cfg, mar_annual=0)
        self.assertNotEqual(actual.sortino, zero.sortino)
        self.assertTrue(drawdown_events(equity, 100, config=cfg).empty)
        self.assertFalse(drawdown_events(equity, 100, config=cfg, threshold=-.001).empty)

    def test_csv_cache_range_and_immutability(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from backtest import CachedCsvDataSource
        with TemporaryDirectory() as directory:
            path = Path(directory) / "A_2024-01-01_2024-05-06_adjusted.csv"
            source().get_bars("A", "2024-01-01", "2024-05-06").to_csv(path)
            data = CachedCsvDataSource(directory, cache_dirs=["."])
            bars = data.get_bars("A", "2024-02-01", "2024-03-01")
            original = bars.copy()
            bars.iloc[0, 0] = 1
            pd.testing.assert_frame_equal(original, data.get_bars("A", "2024-02-01", "2024-03-01"))
            with self.assertRaises(FileNotFoundError):
                data.get_bars("A", "2023-01-01", "2024-03-01")


if __name__ == "__main__":
    unittest.main()
