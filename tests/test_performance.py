"""Calendar slices retain the prior close and use full-series downside RMS."""

import unittest

import numpy as np
import pandas as pd

from backtest.performance import (
    performance_metrics, calendar_year_metrics, equity_returns, equity_drawdown,
    calendar_month_returns, monthly_return_statistics, drawdown_events,
)


class PerformanceTests(unittest.TestCase):
    def test_cagr_and_drawdown_include_opening_capital(self):
        values = pd.Series([90., 110.], index=pd.to_datetime(["2023-01-01", "2024-01-01"]))
        metrics = performance_metrics(values, 100.)
        self.assertAlmostEqual(metrics.nav, 1.1)
        self.assertAlmostEqual(metrics.cumulative_return, .1)
        self.assertAlmostEqual(metrics.annualized_return, 1.1 ** (365.25 / 365) - 1)
        self.assertAlmostEqual(metrics.max_drawdown, .1)
        self.assertAlmostEqual(metrics.calmar, metrics.annualized_return / .1)

    def test_sortino_uses_all_observations_and_daily_mar(self):
        returns = np.array([.01, -.02, .03])
        values = pd.Series(100 * np.cumprod(1 + returns),
                           index=pd.date_range("2024-01-01", periods=3))
        for annual_mar in (0., .05):
            metrics = performance_metrics(values, 100, mar_annual=annual_mar)
            daily_mar = (1 + annual_mar) ** (1 / 252) - 1
            excess = returns - daily_mar
            downside = np.sqrt(np.mean(np.minimum(excess, 0) ** 2))
            expected = excess.mean() / downside * np.sqrt(252)
            self.assertAlmostEqual(metrics.sortino, expected)

    def test_years_reconcile_and_include_first_return(self):
        values = pd.Series([90., 81., 108., 108.],
                           index=pd.to_datetime(["2021-12-31", "2022-01-04", "2022-12-30", "2023-01-03"]))
        annual = calendar_year_metrics(values, 100.)
        self.assertAlmostEqual(annual.loc[2022, "opening_equity"], 90.)
        self.assertAlmostEqual(annual.loc[2022, "cumulative_return"], .2)
        self.assertAlmostEqual(annual.loc[2022, "max_drawdown"], .1)
        self.assertAlmostEqual(np.prod(1 + annual.cumulative_return), 108 / 100)
        self.assertEqual(annual.loc[2022, "period_end"], pd.Timestamp("2022-12-31"))
        self.assertTrue(annual.loc[2022, "complete_calendar_year"])
        self.assertAlmostEqual(equity_returns(values.iloc[1:], 90.).iloc[0], -.1)

    def test_partial_final_year_is_labeled_and_annualized(self):
        values = pd.Series([100., 110.], index=pd.to_datetime(["2025-12-31", "2026-03-18"]))
        annual = calendar_year_metrics(values, 100.)
        self.assertFalse(annual.loc[2026, "complete_calendar_year"])
        days = (pd.Timestamp("2026-03-18") - pd.Timestamp("2025-12-31")).days
        self.assertAlmostEqual(annual.loc[2026, "annualized_return"], 1.1 ** (365.25 / days) - 1)

    def test_zero_denominators_are_nan_not_infinite(self):
        values = pd.Series([100., 100., 100.], index=pd.date_range("2024-01-01", periods=3))
        metrics = performance_metrics(values, 100.)
        self.assertTrue(np.isnan(metrics.simplified_sharpe))
        self.assertTrue(np.isnan(metrics.calmar))
        self.assertTrue(np.isnan(metrics.sortino))
        self.assertAlmostEqual(metrics.annualized_return, 0.)

    def test_invalid_equity_is_rejected(self):
        for sequence in ([100, 0], [100, np.nan]):
            with self.assertRaises(ValueError):
                performance_metrics(pd.Series(sequence, index=pd.date_range("2024-01-01", periods=2)), 100)
        values = pd.Series([100, 101], index=pd.to_datetime(["2024-01-02", "2024-01-01"]))
        with self.assertRaises(ValueError):
            equity_drawdown(values, 100)


class DistributionAndDrawdownTests(unittest.TestCase):
    def test_months_include_initial_fees_and_partial_last_month(self):
        values = pd.Series([99., 110., 99., 108.9], index=pd.to_datetime([
            "2024-01-05", "2024-01-31", "2024-02-29", "2024-03-18"]))
        returns = calendar_month_returns(values, 100)
        np.testing.assert_allclose(returns, [.1, -.1, .1])
        self.assertEqual(str(returns.index[-1]), "2024-03")
        self.assertAlmostEqual(np.prod(1 + returns), 108.9 / 100)
        with self.assertRaises(ValueError):
            calendar_month_returns(values.iloc[[0, 3]], 100)

    def test_monthly_statistics_zero_months_and_streaks(self):
        values = pd.Series([.1, .2, 0., -.1, -.2, -.1, .3],
                           index=pd.period_range("2024-01", periods=7, freq="M"))
        stats = monthly_return_statistics(values)
        self.assertEqual(stats.total_months, 7)
        self.assertEqual(stats.winning_months, 3)
        self.assertEqual(stats.losing_months, 3)
        self.assertEqual(stats.flat_months, 1)
        self.assertAlmostEqual(stats.win_rate, 3 / 7)
        self.assertAlmostEqual(stats.average_winning_return, .2)
        self.assertAlmostEqual(stats.average_losing_return, -.4 / 3)
        self.assertAlmostEqual(stats.payoff_ratio, 1.5)
        self.assertAlmostEqual(stats.profit_factor, 1.5)
        self.assertEqual(stats.longest_winning_streak, 2)
        self.assertEqual(stats.longest_losing_streak, 3)
        self.assertEqual(stats.best_month, .3)
        self.assertEqual(stats.worst_month, -.2)

    def test_monthly_statistics_undefined_ratios_and_invalid_series(self):
        months = pd.period_range("2024-01", periods=2, freq="M")
        stats = monthly_return_statistics(pd.Series([0., .1], index=months))
        self.assertTrue(np.isnan(stats.payoff_ratio))
        self.assertTrue(np.isnan(stats.profit_factor))
        self.assertTrue(np.isnan(stats.average_losing_return))
        stats = monthly_return_statistics(pd.Series([-.1, -.2], index=months))
        self.assertEqual(stats.profit_factor, 0.)
        self.assertTrue(np.isnan(stats.payoff_ratio))
        for values in (pd.Series([.1, np.nan], index=months),
                       pd.Series([.1, .2], index=months[::-1])):
            with self.assertRaises(ValueError):
                monthly_return_statistics(values)

    def test_drawdown_threshold_peak_dates_and_true_recovery(self):
        values = pd.Series([100., 100., 99.95, 99.8, 95., 99.99, 100., 98.],
                           index=pd.date_range("2024-01-01", periods=8))
        events = drawdown_events(values, 100)
        self.assertEqual(len(events), 2)
        first, last = events.iloc[0], events.iloc[1]
        self.assertEqual(first.start_date, pd.Timestamp("2024-01-02"))
        self.assertEqual(first.trigger_date, pd.Timestamp("2024-01-04"))
        self.assertEqual(first.trough_date, pd.Timestamp("2024-01-05"))
        self.assertEqual(first.recovery_date, pd.Timestamp("2024-01-07"))
        self.assertAlmostEqual(first.depth, -.05)
        self.assertEqual(first.decline_days, 3)
        self.assertEqual(first.recovery_days, 2)
        self.assertTrue(first.recovered)
        self.assertFalse(last.recovered)
        self.assertTrue(pd.isna(last.recovery_date))
        self.assertTrue(pd.isna(last.recovery_days))
        self.assertEqual(last.last_observed_date, values.index[-1])
        self.assertAlmostEqual(events.depth.min(), equity_drawdown(values, 100).min())

    def test_drawdown_opening_loss_empty_events_and_calendar_days(self):
        values = pd.Series([99., 98., 100.],
                           index=pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-10"]))
        event = drawdown_events(values, 100).iloc[0]
        self.assertEqual(event.start_date, values.index[0])
        self.assertEqual(event.decline_days, 3)
        self.assertEqual(event.recovery_days, 2)
        rising = pd.Series([101., 102., 103.], index=values.index)
        self.assertTrue(drawdown_events(rising, 100).empty)
        shallow = pd.Series([100., 99.95, 100.], index=values.index)
        self.assertTrue(drawdown_events(shallow, 100).empty)
        for threshold in (0., -1., np.nan):
            with self.assertRaises(ValueError):
                drawdown_events(values, 100, threshold=threshold)


if __name__ == "__main__":
    unittest.main()
