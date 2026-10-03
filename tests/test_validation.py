"""Validation plans are deterministic, bounded and independent of execution."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import ast
import unittest

import numpy as np
import pandas as pd

from backtest import (DataFrameDataSource, DateSplit, IndexSplit, load_config,
                      walk_forward_splits, time_series_cv_splits, random_cv_splits)


class WalkForwardTests(unittest.TestCase):
    def test_reference_rolling_and_anchored_fifteen_folds(self):
        rolling = walk_forward_splits("2017-01-01", "2026-05-06")
        anchored = walk_forward_splits("2017-01-01", "2026-05-06", mode="anchored")
        self.assertEqual(len(rolling), 15)
        self.assertEqual(len(anchored), 15)
        self.assertEqual(rolling[0].to_dict(), {
            "train_start": "2017-01-01", "train_end": "2018-12-31",
            "validation_start": "2019-01-01", "validation_end": "2019-06-30"})
        self.assertEqual(rolling[1].train_start, "2017-07-01")
        self.assertEqual(rolling[1].train_end, "2019-06-30")
        self.assertTrue(all(f.train_start == "2017-01-01" for f in anchored))
        self.assertEqual(rolling[-1].validation_end, "2026-05-06")
        self.assertEqual(anchored[-1].validation_end, "2026-05-06")
        for a, b in zip(rolling, anchored):
            self.assertEqual(a.train_end, b.train_end)
            self.assertEqual(a.validation_start, b.validation_start)
            self.assertEqual(a.validation_end, b.validation_end)
            self.assertLess(a.train_end, a.validation_start)

    def test_article_dates_test_windows_are_contiguous(self):
        folds = walk_forward_splits("2021-01-01", "2026-03-18")
        self.assertEqual(len(folds), 7)
        for a, b in zip(folds, folds[1:]):
            self.assertEqual(pd.Timestamp(a.validation_end) + pd.Timedelta(days=1),
                             pd.Timestamp(b.validation_start))

    def test_calendar_days_gap_and_partial(self):
        folds = walk_forward_splits("2024-01-01", "2024-01-14",
            train_period="5D", test_period="4D", gap_days=2)
        self.assertEqual(folds[0], DateSplit("2024-01-01", "2024-01-05", "2024-01-08", "2024-01-11"))
        self.assertEqual(folds[-1].validation_end, "2024-01-14")
        full = walk_forward_splits("2024-01-01", "2024-01-14",
            train_period="5D", test_period="4D", gap_days=2, include_partial=False)
        self.assertEqual(full, folds[:1])

    def test_no_test_data_returns_empty_and_single_day_is_valid(self):
        self.assertEqual(walk_forward_splits("2024-01-01", "2024-12-31"), ())
        folds = walk_forward_splits("2024-01-01", "2024-01-06", train_period="5D", test_period="4D")
        self.assertEqual(folds[0].validation_start, folds[0].validation_end)

    def test_custom_step_can_overlap_or_leave_gaps(self):
        overlap = walk_forward_splits("2024-01-01", "2024-01-31",
            train_period="10D", test_period="8D", step="4D")
        spaced = walk_forward_splits("2024-01-01", "2024-01-31",
            train_period="10D", test_period="4D", step="8D")
        self.assertLess(overlap[1].validation_start, overlap[0].validation_end)
        self.assertGreater(spaced[1].validation_start, spaced[0].validation_end)

    def test_calendar_offsets_and_month_end_anchor(self):
        folds = walk_forward_splits("2024-01-31", "2024-06-30",
            train_period="1m", test_period="1M", step="1M")
        self.assertEqual(folds[0].train_end, "2024-02-28")
        self.assertEqual(folds[1].train_start, "2024-02-29")
        self.assertEqual(folds[2].train_start, "2024-03-31")

    def test_explicit_arguments_override_yaml_and_none_step_tracks_test(self):
        config = load_config({"validation": {"walk_forward": {
            "train_period": "10D", "test_period": "4D", "step": "8D", "mode": "anchored"}}})
        before = config.to_dict()
        folds = walk_forward_splits("2024-01-01", "2024-02-01", config=config,
                                    mode="rolling", test_period="2D", step=None)
        self.assertEqual(folds[1].train_start, "2024-01-03")
        self.assertEqual(config.to_dict(), before)

    def test_invalid_periods_flags_and_dates(self):
        for key in ("train_period", "test_period", "step"):
            for value in ("0D", "-1D", "1W", "", 5):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    walk_forward_splits("2024-01-01", "2026-01-01", **{key: value})
        for options in ({"mode": "random"}, {"gap_days": -1}, {"gap_days": True},
                        {"gap_days": 1.5}, {"include_partial": "false"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                walk_forward_splits("2024-01-01", "2026-01-01", **options)
        for start, end in (("2025-01-01", "2024-01-01"), ("NaT", "2025-01-01"),
                           ("2024-01-01T10:00", "2025-01-01"),
                           (pd.Timestamp("2024-01-01", tz="UTC"), "2025-01-01")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                walk_forward_splits(start, end)

    def test_date_split_validation_and_immutability(self):
        with self.assertRaises(ValueError):
            DateSplit("2024-01-01", "2024-01-05", "2024-01-05", "2024-01-10")
        fold = DateSplit("2024-01-01", "2024-01-05", "2024-01-06", "2024-01-10")
        with self.assertRaises(FrozenInstanceError):
            fold.train_start = "2030-01-01"

    def test_works_with_existing_bounded_source_without_engine_changes(self):
        dates = pd.bdate_range("2024-01-01", "2024-03-31")
        prices = pd.DataFrame({"Open": np.arange(len(dates)) + 10.,
                               "Close": np.arange(len(dates)) + 11.}, index=dates)
        source = DataFrameDataSource({"A": prices})
        folds = walk_forward_splits("2024-01-01", "2024-03-31", train_period="1M", test_period="1M")
        for fold in folds:
            parts = source.split(**fold.to_dict())
            train = parts.train.get_bars("A", "2023-01-01", "2030-01-01")
            validation = parts.validation.get_bars("A", "2023-01-01", "2030-01-01")
            self.assertGreaterEqual(train.index.min(), pd.Timestamp(fold.train_start))
            self.assertLessEqual(train.index.max(), pd.Timestamp(fold.train_end))
            self.assertGreaterEqual(validation.index.min(), pd.Timestamp(fold.validation_start))
            self.assertLessEqual(validation.index.max(), pd.Timestamp(fold.validation_end))
            self.assertTrue(train.index.intersection(validation.index).empty)


class TimeSeriesCVTests(unittest.TestCase):
    def test_expanding_blocks_are_past_only_and_cover_end(self):
        folds = time_series_cv_splits("2024-01-01", "2024-01-13", n_splits=3)
        self.assertEqual(folds, (
            DateSplit("2024-01-01", "2024-01-03", "2024-01-04", "2024-01-06"),
            DateSplit("2024-01-01", "2024-01-06", "2024-01-07", "2024-01-09"),
            DateSplit("2024-01-01", "2024-01-09", "2024-01-10", "2024-01-13")))

    def test_fixed_mode_uses_previous_block(self):
        folds = time_series_cv_splits("2024-01-01", "2024-01-12", n_splits=3, expanding=False)
        self.assertEqual([f.train_start for f in folds], ["2024-01-01", "2024-01-04", "2024-01-07"])
        self.assertEqual(folds[-1].validation_end, "2024-01-12")

    def test_gap_and_yaml_override(self):
        config = load_config({"validation": {"time_series_cv": {
            "n_splits": 3, "gap_days": 1, "expanding": False}}})
        folds = time_series_cv_splits("2024-01-01", "2024-01-12", config=config, expanding=True)
        self.assertEqual(folds[0].validation_start, "2024-01-05")
        self.assertTrue(all(f.train_start == "2024-01-01" for f in folds))

    def test_invalid_sizes_gaps_and_expanding(self):
        for options in ({"n_splits": 1}, {"n_splits": True}, {"n_splits": 2.5},
                        {"n_splits": 20}, {"gap_days": 5}, {"gap_days": -1},
                        {"expanding": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                time_series_cv_splits("2024-01-01", "2024-01-12", **options)


class RandomCVTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2024-01-01", periods=23)

    def test_each_row_validates_once_and_each_fold_partitions_all_rows(self):
        folds = random_cv_splits(self.dates)
        held_out = []
        for fold in folds:
            a, b = set(fold.train_indices), set(fold.validation_indices)
            self.assertFalse(a & b)
            self.assertEqual(a | b, set(range(len(self.dates))))
            self.assertEqual(list(fold.train_indices), sorted(fold.train_indices))
            self.assertEqual(list(fold.validation_indices), sorted(fold.validation_indices))
            held_out.extend(fold.validation_indices)
        self.assertEqual(sorted(held_out), list(range(len(self.dates))))
        sizes = [len(f.validation_indices) for f in folds]
        self.assertLessEqual(max(sizes) - min(sizes), 1)
        self.assertTrue(any(not f.is_chronological for f in folds))

    def test_seed_reproducibility_and_local_rng(self):
        a = random_cv_splits(self.dates, seed=7)
        self.assertEqual(a, random_cv_splits(self.dates, seed=7))
        self.assertNotEqual(a, random_cv_splits(self.dates, seed=8))
        before = np.random.get_state()
        random_cv_splits(self.dates)
        after = np.random.get_state()
        self.assertEqual(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])

    def test_exact_dates_and_take_do_not_expand_to_envelopes(self):
        frame = pd.DataFrame({"Close": np.arange(23.)}, index=self.dates)
        fold = random_cv_splits(self.dates)[0]
        a, b = fold.take(frame)
        pd.testing.assert_frame_equal(a, frame.iloc[list(fold.train_indices)])
        pd.testing.assert_frame_equal(b, frame.iloc[list(fold.validation_indices)])
        self.assertEqual(tuple(b.index), fold.validation_dates)
        self.assertEqual(tuple(a.index), fold.train_dates)
        b.iloc[0, 0] = -999
        self.assertFalse((frame.Close == -999).any())
        with self.assertRaises(ValueError):
            fold.take(frame.iloc[::-1])
        with self.assertRaises(ValueError):
            fold.take(frame.iloc[:-1])
        with self.assertRaises(TypeError):
            fold.take(frame.Close.to_numpy())

    def test_yaml_and_explicit_seed(self):
        config = load_config({"validation": {"random_cv": {"n_splits": 3, "seed": 6}}})
        self.assertEqual(len(random_cv_splits(self.dates, config=config)), 3)
        self.assertEqual(random_cv_splits(self.dates, config=config, seed=9),
                         random_cv_splits(self.dates, n_splits=3, seed=9))

    def test_invalid_calendars(self):
        calendars = ([], self.dates[::-1], self.dates.insert(0, self.dates[0]),
                     self.dates.tz_localize("UTC"), [pd.NaT], [pd.Timestamp("2024-01-01T12:00")])
        for dates in calendars:
            with self.subTest(dates=str(dates)[:60]), self.assertRaises(ValueError):
                random_cv_splits(dates)

    def test_invalid_sizes_seed_and_index_records(self):
        for options in ({"n_splits": 1}, {"n_splits": 24}, {"n_splits": False},
                        {"seed": -1}, {"seed": None}, {"seed": 1.5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                random_cv_splits(self.dates, **options)
        for train, validation in (([0], [0]), ([1, 0], [2]), ([0, 0], [1]),
                                  ([], [0]), ([0], [99]), ([0], [1])):
            with self.subTest(train=train), self.assertRaises(ValueError):
                IndexSplit(train, validation, tuple(self.dates))

    def test_module_does_not_depend_on_execution_components(self):
        path = Path(__file__).parents[1] / "backtest/validation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        local_imports = {node.module for node in ast.walk(tree)
                         if isinstance(node, ast.ImportFrom) and node.level}
        self.assertEqual(local_imports, {"configuration"})


if __name__ == "__main__":
    unittest.main()
