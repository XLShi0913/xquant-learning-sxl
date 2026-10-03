"""Offline Yahoo adapter tests: configuration, caching and provider isolation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from backtest import CachedCsvDataSource, YFinanceDataSource, create_data_source, load_config


class YahooTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_config({"data": {"provider": "yfinance",
            "cache_dirs": ["old"], "download_if_missing": True,
            "yfinance": {"cache_dir": "new", "timeout": 7}}})
        self.bars = pd.DataFrame({"Open": [10., 11., 12.], "Close": [11., 12., 13.]},
            index=pd.date_range("2024-01-02", periods=3, name="Date"))

    def source(self, downloader, **kwargs):
        return YFinanceDataSource(self.root, config=self.config, downloader=downloader, **kwargs)

    def test_factory_and_example_configuration(self):
        self.assertIsInstance(create_data_source(self.config, root=self.root), YFinanceDataSource)
        self.assertIsInstance(create_data_source(root=self.root), CachedCsvDataSource)
        example = load_config(Path(__file__).parents[1] / "backtest/config/yfinance.yaml")
        self.assertEqual(example.section("data")["provider"], "yfinance")
        self.assertTrue(example.section("data")["download_if_missing"])

    def test_download_inclusive_dates_parameters_and_reuse(self):
        downloader = Mock(return_value=self.bars)
        source = self.source(downloader, timeout=9, progress=True)
        result = source.get_bars("510300.SS", "2024-01-02", "2024-01-03")
        kwargs = downloader.call_args.kwargs
        self.assertEqual(kwargs["end"], "2024-01-04")
        self.assertEqual(kwargs["start"], "2024-01-02")
        self.assertEqual(kwargs["interval"], "1d")
        self.assertEqual(kwargs["timeout"], 9)
        self.assertTrue(kwargs["progress"])
        self.assertTrue(kwargs["auto_adjust"])
        self.assertFalse(kwargs["multi_level_index"])
        pd.testing.assert_frame_equal(result, self.bars.iloc[:2], check_freq=False)
        result.iloc[0, 0] = 999
        self.assertEqual(source.get_bars("510300.SS", "2024-01-02", "2024-01-03").iloc[0, 0], 10)
        self.assertEqual(downloader.call_count, 1)
        reader = CachedCsvDataSource(self.root, config={"data": {"cache_dirs": ["new"]}})
        pd.testing.assert_frame_equal(reader.get_bars("510300.SS", "2024-01-02", "2024-01-03"),
                                     self.bars.iloc[:2], check_freq=False)

    def test_existing_course_cache_needs_no_download(self):
        directory = self.root / "old"
        directory.mkdir()
        self.bars.to_csv(directory / "510300.SS_2024-01-01_2024-01-05_adjusted.csv")
        downloader = Mock(side_effect=AssertionError("must not contact Yahoo"))
        result = self.source(downloader).get_bars("510300.SS", "2024-01-02", "2024-01-04")
        pd.testing.assert_frame_equal(result, self.bars, check_freq=False)
        downloader.assert_not_called()

    def test_offline_missing_cache_does_not_download(self):
        downloader = Mock()
        with self.assertRaises(FileNotFoundError):
            self.source(downloader, download_if_missing=False).get_bars(
                "510300.SS", "2024-01-02", "2024-01-04")
        downloader.assert_not_called()

    def test_failure_does_not_create_cache(self):
        invalid = self.bars.copy()
        invalid.iloc[0, 0] = -1
        for frame in (None, self.bars.iloc[:0], invalid):
            with self.subTest(frame=str(type(frame))):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.source(Mock(return_value=frame)).get_bars(
                        "510300.SS", "2024-01-02", "2024-01-04")
                self.assertFalse((self.root / "new").exists())

    def test_network_error_is_not_silently_replaced(self):
        downloader = Mock(side_effect=ConnectionError("Yahoo unavailable"))
        with self.assertRaisesRegex(ConnectionError, "Yahoo unavailable"):
            self.source(downloader).get_bars("510300.SS", "2024-01-02", "2024-01-04")
        self.assertEqual(downloader.call_count, 1)
        self.assertFalse((self.root / "new").exists())

    def test_raw_and_adjusted_caches_are_separate(self):
        adjusted = Mock(return_value=self.bars)
        self.source(adjusted).get_bars("A", "2024-01-02", "2024-01-04")
        raw = Mock(return_value=self.bars * 2)
        raw_source = self.source(raw, auto_adjust=False)
        result = raw_source.get_bars("A", "2024-01-02", "2024-01-04")
        self.assertEqual(result.iloc[0, 0], 20)
        self.assertFalse(raw.call_args.kwargs["auto_adjust"])
        self.assertEqual(len(list((self.root / "new").glob("*.csv"))), 2)

    def test_legacy_cache_download_uses_yahoo_configuration(self):
        downloader = Mock(return_value=self.bars)
        with patch.dict("sys.modules", {"yfinance": SimpleNamespace(download=downloader)}):
            reader = CachedCsvDataSource(self.root, config=self.config)
            reader.get_bars("A", "2024-01-02", "2024-01-04")
        self.assertEqual(downloader.call_args.kwargs["timeout"], 7)
        self.assertTrue((self.root / "old/A_2024-01-02_2024-01-04_adjusted.csv").exists())

    def test_invalid_options_rejected(self):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"auto_adjust": "true"},
                        {"download_if_missing": "false"}, {"threads": 2}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.source(Mock(), **options)


if __name__ == "__main__":
    unittest.main()
