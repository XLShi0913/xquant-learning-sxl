"""Public API for the learning backtest framework."""

from .account import Account
from .broker import SimBroker
from .data import DataFrameDataSource, MarketDataSource, CachedCsvDataSource, DataSplit, WindowDataSource
from .configuration import Config, load_config
from .costs import TransactionCostModel, SlippageModel, FixedRateSlippage
from .alpaca import AlpacaDataSource, AlpacaDataError
from .yahoo import YFinanceDataSource
from .engine import Engine
from .models import AccountSnapshot, AccountView, MarketHistory, Order, OrderResult, Trade
from .strategy import Strategy
from .research import (BacktestResult, run_backtest, create_data_source, scan_cases,
                       scan_parameter, scan_tied_windows, scan_grid, scan_metrics, select_best)

__all__ = [
    "Account", "AccountSnapshot", "AccountView", "DataFrameDataSource", "Engine",
    "MarketDataSource", "MarketHistory", "Order", "OrderResult", "SimBroker",
    "Strategy", "Trade",
    "Config", "load_config", "TransactionCostModel", "SlippageModel", "FixedRateSlippage",
    "AlpacaDataSource", "AlpacaDataError", "CachedCsvDataSource", "DataSplit", "WindowDataSource",
    "YFinanceDataSource",
    "BacktestResult", "run_backtest", "create_data_source", "scan_cases", "scan_parameter",
    "scan_tied_windows", "scan_grid", "scan_metrics", "select_best",
]
