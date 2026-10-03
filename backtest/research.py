"""Fresh-run factories, parameter scans and explicit train/validation workflows."""
from dataclasses import dataclass
from itertools import product
from collections.abc import Mapping

import numpy as np
import pandas as pd

from .account import Account
from .allocation import PeriodicAllocationStrategy, BuyAndHoldEqualWeightStrategy
from .alpaca import AlpacaDataSource
from .broker import SimBroker
from .configuration import UNSET, configured, load_config
from .data import CachedCsvDataSource
from .yahoo import YFinanceDataSource
from .engine import Engine
from .performance import performance_metrics, calendar_year_metrics
from .risk_controls import ProtectedAllocationStrategy


@dataclass
class BacktestResult:
    engine: Engine
    ledger: pd.DataFrame
    metrics: pd.Series
    annual: pd.DataFrame

    @property
    def equity(self):
        return self.ledger["portfolio_value"]


def create_data_source(config=None, *, root=None):
    settings = load_config(config)
    provider = settings.section("data")["provider"]
    if provider == "cache":
        return CachedCsvDataSource(root, config=settings)
    if provider == "alpaca":
        return AlpacaDataSource(config=settings)
    if provider == "yfinance":
        return YFinanceDataSource(root, config=settings)
    raise ValueError("data.provider must be cache, yfinance or alpaca; no implicit provider fallback")


def run_backtest(source=None, *, config=None, start=UNSET, end=UNSET, symbols=UNSET,
                 method=UNSET, momentum_window=UNSET, volatility_window=UNSET,
                 interval=UNSET, stop_loss=UNSET, take_profit=UNSET,
                 initial_cash=UNSET, broker_parameters=None, strategy_factory=None):
    """One independent run. Arguments > YAML; explicit None disables protection.

    strategy_factory(config) may provide any Strategy. Built-in allocation
    strategies use same-close research, not next-day executable close signals.
    Broker parameters belong only to simulation, not indicator calculations.
    """
    cfg = load_config(config)
    market, strategy, broker = (cfg.section(k) for k in ("market", "strategy", "broker"))
    cfg = cfg.override(
        market={"start": configured(start, market, "start"), "end": configured(end, market, "end"),
                "symbols": configured(symbols, market, "symbols")},
        strategy={k: configured(v, strategy, k) for k, v in {
            "method": method, "momentum_window": momentum_window, "volatility_window": volatility_window,
            "interval": interval, "stop_loss": stop_loss, "take_profit": take_profit}.items()},
        broker={**(broker_parameters or {}), "initial_cash": (
            (broker_parameters or {}).get("initial_cash", broker["initial_cash"])
            if initial_cash is UNSET else initial_cash)})
    market, strategy = cfg.section("market"), cfg.section("strategy")
    source = create_data_source(cfg) if source is None else source
    account = Account(symbols=market["symbols"], config=cfg)
    sim = SimBroker(source, market["start"], market["end"], market["symbols"], config=cfg)
    if strategy_factory is not None:
        policy = strategy_factory(cfg)
    elif strategy["method"] == "buy_hold":
        policy = BuyAndHoldEqualWeightStrategy(config=cfg)
    elif strategy["stop_loss"] is not None or strategy["take_profit"] is not None:
        policy = ProtectedAllocationStrategy(config=cfg)
    else:
        policy = PeriodicAllocationStrategy(config=cfg)
    engine = Engine(account, sim, policy, config=cfg)
    ledger = engine.run()
    rejected = [r for r in sim.order_history if r.status == "REJECTED"]
    if rejected:
        raise RuntimeError(f"backtest had {len(rejected)} rejected orders: {rejected[0].reason}")
    cash = account.initial_cash
    for trade in sim.trades:
        cash += (-1 if trade.side == "BUY" else 1) * trade.shares * trade.price - trade.fees
        if not np.isclose(cash, trade.cash_after, rtol=0, atol=1e-6):
            raise RuntimeError("cash ledger does not reconcile with fills, commission and tax")
    if (ledger["cash"] < -1e-6).any():
        raise RuntimeError("negative cash in backtest")
    positions = ledger[[f"position_{s}" for s in market["symbols"]]].to_numpy()
    prices = ledger[[f"close_{s}" for s in market["symbols"]]].to_numpy()
    if (positions < 0).any() or (positions % sim.lot_size).any():
        raise RuntimeError("positions violate long-only / round-lot constraints")
    np.testing.assert_allclose(ledger.portfolio_value,
                               ledger.cash + (positions * prices).sum(axis=1), atol=1e-7)
    if getattr(policy, "audit", None):
        audit = pd.DataFrame(policy.audit)
        if (audit[[*market["symbols"], "CASH"]].sum(axis=1).sub(1).abs() > 1e-6).any():
            raise RuntimeError("target weights do not sum to one")
    kwargs = {"annual_trading_days": engine.annual_trading_days,
              "mar_annual": cfg.section("analysis")["mar_annual"]}
    equity = ledger["portfolio_value"]
    return BacktestResult(engine, ledger, performance_metrics(equity, account.initial_cash, **kwargs),
                          calendar_year_metrics(equity, account.initial_cash, **kwargs))


def scan_cases(runner, cases):
    """Run named parameter mappings. runner must construct a fresh run per call."""
    if not isinstance(cases, Mapping) or not cases:
        raise ValueError("cases must be a nonempty mapping")
    return {label: runner(**dict(parameters)) for label, parameters in cases.items()}


def scan_parameter(runner, parameter, values, *, fixed=None):
    values = list(values)
    if not values or len(set(values)) != len(values):
        raise ValueError("scan values must be nonempty and unique")
    return scan_cases(runner, {v: {**(fixed or {}), parameter: v} for v in values})


def scan_tied_windows(runner, values, *, fields=("momentum_window", "volatility_window"), fixed=None):
    """Move momentum and volatility together (risk parity may pass only volatility)."""
    values = list(values)
    if not values or len(set(values)) != len(values) or not fields or len(set(fields)) != len(fields):
        raise ValueError("scan values and fields must be nonempty and unique")
    return scan_cases(runner, {v: {**(fixed or {}), **dict.fromkeys(fields, v)} for v in values})


def scan_grid(runner, grid, *, fixed=None):
    """Cartesian scan for deliberately independent parameters, not tied windows."""
    dimensions = {k: list(v) for k, v in grid.items()}
    if not dimensions or any(not v or len(set(v)) != len(v) for v in dimensions.values()):
        raise ValueError("grid dimensions cannot be empty")
    keys = tuple(dimensions)
    return scan_cases(runner, {point: {**(fixed or {}), **dict(zip(keys, point))}
                             for point in product(*(dimensions[k] for k in keys))})


def scan_metrics(results):
    """Metric table for BacktestResult scans; keys remain the parameter values."""
    return pd.DataFrame({k: result.metrics for k, result in results.items()}).T


def select_best(results, metric="simplified_sharpe"):
    """Maximum finite score. Use training results only to select parameters."""
    scores = {k: float(v.metrics[metric]) for k, v in results.items()}
    valid = {k: v for k, v in scores.items() if np.isfinite(v)}
    if not valid:
        raise ValueError(f"no finite values for selection metric {metric}")
    return max(valid, key=valid.get)
