"""Public backtest exports and direct module imports share class identities."""

import importlib
import unittest

import backtest


class ImportCompatibilityTests(unittest.TestCase):
    def test_public_exports_are_identical(self):
        origins = {
            "account": ["Account"],
            "broker": ["SimBroker"],
            "data": ["DataFrameDataSource", "MarketDataSource", "CachedCsvDataSource", "DataSplit", "WindowDataSource"],
            "configuration": ["Config", "load_config"],
            "costs": ["TransactionCostModel", "SlippageModel", "FixedRateSlippage"],
            "alpaca": ["AlpacaDataSource", "AlpacaDataError"],
            "yahoo": ["YFinanceDataSource"],
            "research": ["BacktestResult", "run_backtest", "create_data_source", "scan_cases",
                         "scan_parameter", "scan_tied_windows", "scan_grid", "scan_metrics", "select_best"],
            "engine": ["Engine"],
            "models": ["AccountSnapshot", "AccountView", "MarketHistory", "Order", "OrderResult", "Trade"],
            "strategy": ["Strategy"],
        }
        self.assertEqual(set(backtest.__all__), {n for names in origins.values() for n in names})
        for module, names in origins.items():
            imported = importlib.import_module(f"backtest.{module}")
            for name in names:
                self.assertIs(getattr(imported, name), getattr(backtest, name))

    def test_submodules_are_identical(self):
        for name in ("account", "allocation", "broker", "data", "engine", "models", "strategy", "risk_controls"):
            imported = importlib.import_module(f"backtest.{name}")
            self.assertIs(
                getattr(backtest, name),
                imported,
            )


if __name__ == "__main__":
    unittest.main()
