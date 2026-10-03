"""Net-equity metrics and continuous-backtest calendar-year attribution.

CAGR uses elapsed calendar days / 365.25. Sharpe assumes a zero risk-free rate.
Sortino uses full-series downside RMS relative to daily MAR, not the standard
deviation of the negative-return subset. Zero denominators produce NaN.
"""

import numpy as np
import pandas as pd
from .configuration import UNSET, load_config


METRIC_COLUMNS = [
    "nav", "cumulative_return", "annualized_return", "annualized_volatility",
    "max_drawdown", "simplified_sharpe", "calmar", "sortino",
]


def _validated_equity(equity, initial_value):
    if not np.isfinite(initial_value) or initial_value <= 0:
        raise ValueError("initial_value must be finite and positive")
    if not isinstance(equity, pd.Series) or equity.empty:
        raise ValueError("equity must be a nonempty Series")
    result = equity.astype(float).copy()
    result.index = pd.DatetimeIndex(result.index)
    if result.index.hasnans or result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("equity dates must be unique, valid and increasing")
    if not np.isfinite(result.to_numpy()).all() or (result <= 0).any():
        raise ValueError("equity values must be finite and positive")
    return result


def equity_returns(equity, initial_value):
    """Include the first observation's return against its actual opening equity."""
    values = _validated_equity(equity, initial_value)
    returns = values.pct_change()
    returns.iloc[0] = values.iloc[0] / initial_value - 1
    return returns.rename("daily_return")


def equity_drawdown(equity, initial_value):
    """Within-window drawdown, including opening equity as the initial peak."""
    values = _validated_equity(equity, initial_value)
    peak = np.maximum.accumulate(np.r_[initial_value, values.to_numpy()])[1:]
    return (values / peak - 1).rename("drawdown")


def performance_metrics(equity, initial_value, *, annual_trading_days=None,
                        mar_annual=UNSET, period_start=None, period_end=None, config=None):
    """Eight metrics from after-cost equity, with optional calendar boundaries.

    period_start/end describe the time between opening and closing valuations.
    Annual slices can therefore carry the last quote to December 31 on holidays.
    nav is closing / opening equity (for yearly slices, each opening NAV is 1).
    The first daily return includes any opening-day fee or price change.
    """
    if annual_trading_days is None or mar_annual is UNSET:
        settings = load_config(config)
        annual_trading_days = settings.section("engine")["annual_trading_days"] if annual_trading_days is None else annual_trading_days
        mar_annual = settings.section("analysis")["mar_annual"] if mar_annual is UNSET else mar_annual
    values = _validated_equity(equity, initial_value)
    if isinstance(annual_trading_days, bool) or not isinstance(annual_trading_days, int) or annual_trading_days <= 0:
        raise ValueError("annual_trading_days must be a positive integer")
    if not np.isfinite(mar_annual) or mar_annual <= -1:
        raise ValueError("mar_annual must be finite and greater than -1")
    start = values.index[0] if period_start is None else pd.Timestamp(period_start)
    end = values.index[-1] if period_end is None else pd.Timestamp(period_end)
    if pd.isna(start) or pd.isna(end) or start > values.index[0] or end < values.index[-1] or end < start:
        raise ValueError("calendar boundaries must enclose equity observations")
    years = (end - start).total_seconds() / (365.25 * 86400)
    nav = float(values.iloc[-1] / initial_value)
    cagr = float(np.expm1(np.log(nav) / years)) if years > 0 else float("nan")
    returns = equity_returns(values, initial_value)
    sigma = float(returns.std(ddof=1)) if len(returns) > 1 else float("nan")
    annual_vol = sigma * np.sqrt(annual_trading_days)
    sharpe = float(returns.mean() / sigma * np.sqrt(annual_trading_days)) if sigma > 0 else float("nan")
    mdd = max(0.0, float(-equity_drawdown(values, initial_value).min()))
    calmar = cagr / mdd if mdd > 0 else float("nan")
    mar_daily = float(np.expm1(np.log1p(mar_annual) / annual_trading_days))
    excess = returns.to_numpy() - mar_daily
    downside = float(np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2)))
    sortino = float(excess.mean() / downside * np.sqrt(annual_trading_days)) if downside > 0 else float("nan")
    return pd.Series(dict(zip(METRIC_COLUMNS, [
        nav, nav - 1, cagr, annual_vol, mdd, sharpe, calmar, sortino,
    ])), dtype=float)


def calendar_year_metrics(equity, initial_value, *, annual_trading_days=None,
                          mar_annual=UNSET, config=None):
    """Slice one continuous run without resetting positions, signals or orders.

    Each year starts with the previous year's last equity (or original capital).
    Annual drawdown peaks reset to that opening equity. Complete years use
    calendar year boundaries; the first/last year use actual observation edges.
    For leap years, CAGR still uses the same 365.25-day basis as total metrics.
    """
    if annual_trading_days is None or mar_annual is UNSET:
        settings = load_config(config)
        annual_trading_days = settings.section("engine")["annual_trading_days"] if annual_trading_days is None else annual_trading_days
        mar_annual = settings.section("analysis")["mar_annual"] if mar_annual is UNSET else mar_annual
    values = _validated_equity(equity, initial_value)
    rows = []
    for year in values.index.year.unique():
        window = values[values.index.year == year]
        previous = values[values.index.year < year]
        opening = float(previous.iloc[-1]) if len(previous) else float(initial_value)
        start = max(values.index[0], pd.Timestamp(year=year - 1, month=12, day=31))
        end = min(values.index[-1], pd.Timestamp(year=year, month=12, day=31))
        metrics = performance_metrics(
            window, opening, annual_trading_days=annual_trading_days,
            mar_annual=mar_annual, period_start=start, period_end=end)
        complete = year > values.index[0].year and end.month == 12 and end.day == 31
        rows.append({
            "year": int(year), **metrics.to_dict(),
            "opening_equity": opening, "closing_equity": float(window.iloc[-1]),
            "global_nav_at_end": float(window.iloc[-1] / initial_value),
            "period_start": start, "period_end": end,
            "first_trading_date": window.index[0], "last_trading_date": window.index[-1],
            "trading_days": len(window), "complete_calendar_year": complete,
        })
    return pd.DataFrame(rows).set_index("year")


def calendar_month_returns(equity, initial_value):
    """Month-end / previous-month-end - 1, including partial boundary months.

    First month opens at initial_value; last closes at the last valuation.
    Entirely missing months are rejected, not invented or skipped.
    """
    values = _validated_equity(equity, initial_value)
    closing = values.groupby(values.index.to_period("M")).last()
    months = pd.period_range(closing.index[0], closing.index[-1], freq="M")
    if not closing.index.equals(months):
        raise ValueError("equity has entirely missing calendar months")
    returns = closing.pct_change()
    returns.iloc[0] = closing.iloc[0] / initial_value - 1
    returns.index.name = "month"
    return returns.rename("monthly_return")


def _longest_streak(mask):
    longest = current = 0
    for matches in mask:
        current = current + 1 if matches else 0
        longest = max(longest, current)
    return longest


def monthly_return_statistics(returns):
    """Statistics of monthly RETURN RATES, not cash profits.

    Zero months count in total months but neither win nor lose and break either
    streak. Missing denominators yield NaN (including no losing months).
    """
    if not isinstance(returns, pd.Series) or returns.empty:
        raise ValueError("returns must be a nonempty monthly Series")
    values = returns.astype(float)
    if not isinstance(values.index, pd.PeriodIndex) or values.index.freqstr != "M":
        raise ValueError("returns must have a monthly PeriodIndex")
    expected = pd.period_range(values.index[0], values.index[-1], freq="M")
    if not values.index.equals(expected) or not np.isfinite(values.to_numpy()).all() or (values <= -1).any():
        raise ValueError("monthly returns must be finite, greater than -1, consecutive and ordered")
    wins, losses = values[values > 0], values[values < 0]
    mean_win = float(wins.mean()) if len(wins) else float("nan")
    mean_loss = float(losses.mean()) if len(losses) else float("nan")
    return pd.Series({
        "total_months": len(values), "winning_months": len(wins),
        "losing_months": len(losses), "flat_months": int((values == 0).sum()),
        "win_rate": len(wins) / len(values),
        "average_winning_return": mean_win, "average_losing_return": mean_loss,
        "payoff_ratio": mean_win / abs(mean_loss) if len(wins) and len(losses) else float("nan"),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else float("nan"),
        "best_month": float(values.max()), "worst_month": float(values.min()),
        "longest_winning_streak": _longest_streak(values > 0),
        "longest_losing_streak": _longest_streak(values < 0),
    })


def drawdown_events(equity, initial_value, *, threshold=None, config=None):
    """Peak-to-recovery episodes whose depth reaches the negative threshold.

    Start is the last high-water mark, not the threshold-crossing day. An event
    ends only upon regaining its peak, even if drawdown becomes shallower than
    threshold. Durations are calendar days. Ongoing events have no recovery date
    or duration; last_observed_date is NOT a fabricated recovery. Opening capital
    is a peak dated at the first observation if its valuation is already lower.
    """
    threshold = load_config(config).section("analysis")["drawdown_threshold"] if threshold is None else threshold
    values = _validated_equity(equity, initial_value)
    if not np.isfinite(threshold) or not -1 < threshold < 0:
        raise ValueError("threshold must be finite and between -1 and 0")
    columns = ["start_date", "trigger_date", "trough_date", "recovery_date",
               "depth", "decline_days", "recovery_days", "recovered",
               "last_observed_date"]
    rows = []
    peak, peak_date = float(initial_value), values.index[0]
    event = None
    for date, value in values.items():
        depth = float(value / peak - 1)
        if value >= peak:
            if event is not None:
                event.update(recovery_date=date, recovered=True,
                             recovery_days=(date - event["trough_date"]).days,
                             last_observed_date=date)
                rows.append(event)
                event = None
            peak, peak_date = float(value), date
        elif event is None and depth <= threshold:
            event = dict(start_date=peak_date, trigger_date=date, trough_date=date,
                         recovery_date=pd.NaT, depth=depth,
                         decline_days=(date - peak_date).days,
                         recovery_days=float("nan"), recovered=False,
                         last_observed_date=date)
        elif event is not None:
            event["last_observed_date"] = date
            if depth < event["depth"]:
                event.update(depth=depth, trough_date=date,
                             decline_days=(date - peak_date).days)
    if event is not None:
        rows.append(event)
    return pd.DataFrame(rows, columns=columns)
