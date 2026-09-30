"""The moved package and its legacy imports share class identities."""

import importlib
import unittest

import backtest
import xquant_learning


class ImportCompatibilityTests(unittest.TestCase):
    def test_public_exports_are_identical(self):
        legacy = importlib.import_module("xquant_learning.backtest")
        self.assertIs(legacy, backtest)
        for name in backtest.__all__:
            self.assertIs(getattr(legacy, name), getattr(backtest, name))
            self.assertIs(getattr(xquant_learning, name), getattr(backtest, name))

    def test_submodules_are_identical(self):
        for name in ("account", "allocation", "broker", "data", "engine", "models", "strategy"):
            self.assertIs(
                importlib.import_module(f"xquant_learning.backtest.{name}"),
                importlib.import_module(f"backtest.{name}"),
            )


if __name__ == "__main__":
    unittest.main()
