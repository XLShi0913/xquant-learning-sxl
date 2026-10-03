"""Synthetic HTTP responses only; these tests do not establish live API access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from backtest import AlpacaDataSource, AlpacaDataError


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def json(self):
        return self.body


class Session:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []
    def get(self, endpoint, **kw):
        self.calls.append((endpoint, kw))
        result = next(self.pages)
        if isinstance(result, Exception):
            raise result
        return result


def bar(date, close=10):
    return {"t": date, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 100}


class AlpacaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {"APCA_API_KEY_ID": "offline-test-key",
                                          "APCA_API_SECRET_KEY": "offline-test-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def adapter(self, pages, **kw):
        # Do not load the user's private credentials during offline tests.
        kw.setdefault("credentials_file", None)
        return AlpacaDataSource(cache_dir=self.directory.name, session=Session(pages), **kw)

    def test_pagination_local_dates_and_cache_without_credentials(self):
        data = self.adapter([
            Response({"bars": {"SPY": [bar("2024-01-02T05:00:00Z")]}, "next_page_token": "second"}),
            Response({"bars": {"SPY": [bar("2024-01-03T05:00:00Z", 11)]}, "next_page_token": None})])
        frame = data.get_bars("SPY", "2024-01-02", "2024-01-03")
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.index[0], pd.Timestamp("2024-01-02"))
        self.assertEqual(len(data.session.calls), 2)
        self.assertEqual(data.session.calls[1][1]["params"]["page_token"], "second")
        self.assertFalse(data.session.calls[0][1]["allow_redirects"])
        self.assertEqual(data.session.calls[0][0], AlpacaDataSource.ENDPOINT)
        for path in Path(self.directory.name).iterdir():
            self.assertNotIn("offline-test-secret", path.read_text())
            self.assertNotIn("offline-test-key", path.read_text())
        with patch.dict(os.environ, {}, clear=True):
            cached = self.adapter([]).get_bars("SPY", "2024-01-02", "2024-01-03")
        pd.testing.assert_frame_equal(cached, frame)

    def test_different_feed_does_not_reuse_cache(self):
        page = Response({"bars": {"SPY": [bar("2024-01-02T05:00:00Z")]}})
        self.adapter([page], feed="iex").get_bars("SPY", "2024-01-02", "2024-01-02")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AlpacaDataError):
                self.adapter([], feed="sip").get_bars("SPY", "2024-01-02", "2024-01-02")

    def test_us_only_and_environment_auth(self):
        data = self.adapter([])
        with self.assertRaises(ValueError):
            data.get_bars("510300.SS", "2024-01-02", "2024-01-03")
        self.assertFalse(data.session.calls)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AlpacaDataError, "browser login"):
                data.get_bars("SPY", "2024-01-02", "2024-01-03")

    def test_http_errors_are_explicit_and_no_cache(self):
        for status in [401, 403, 429, 500, 302]:
            data = self.adapter([Response({}, status)])
            with self.assertRaisesRegex(AlpacaDataError, f"HTTP {status}"):
                data.get_bars("SPY", "2024-01-02", "2024-01-03")
        self.assertFalse(list(Path(self.directory.name).iterdir()))

    def test_repeated_token_and_incomplete_pages_never_cached(self):
        payload = {"bars": {"SPY": [bar("2024-01-02T05:00:00Z")]}, "next_page_token": "x"}
        for data in [self.adapter([Response(payload), Response(payload)]),
                     self.adapter([Response(payload)], max_pages=1)]:
            with self.assertRaises(AlpacaDataError):
                data.get_bars("SPY", "2024-01-02", "2024-01-03")
        self.assertFalse(list(Path(self.directory.name).iterdir()))

    def test_malformed_empty_duplicate_and_network_responses(self):
        pages = [Response({}), Response({"bars": {"SPY": [{"t": "bad"}]}}),
                 Response({"bars": {"SPY": [bar("2024-01-02T05:00:00Z")] * 2}}),
                 requests.ConnectionError("offline-test-secret")]
        for page in pages:
            with self.assertRaises(AlpacaDataError) as caught:
                self.adapter([page]).get_bars("SPY", "2024-01-02", "2024-01-03")
            self.assertNotIn("offline-test-secret", str(caught.exception))
        self.assertFalse(list(Path(self.directory.name).iterdir()))

    def local_file(self, contents=None):
        path = Path(self.directory.name) / "test.local.yaml"
        path.write_text(contents or "endpoint: https://paper-api.alpaca.markets/v2\nkey: local-test-key\nsecret: local-test-secret\n", encoding="utf-8")
        return path

    def page(self):
        return Response({"bars": {"SPY": [bar("2024-01-02T05:00:00Z")]}})

    def test_local_yaml_credentials_and_separate_endpoints(self):
        path = self.local_file()
        data = self.adapter([self.page()], credentials_file=path)
        with patch.dict(os.environ, {}, clear=True):
            data.get_bars("SPY", "2024-01-02", "2024-01-02")
        endpoint, kwargs = data.session.calls[0]
        self.assertEqual(endpoint, AlpacaDataSource.ENDPOINT)
        self.assertEqual(data.trading_endpoint, "https://paper-api.alpaca.markets/v2")
        self.assertEqual(kwargs["headers"]["APCA-API-KEY-ID"], "local-test-key")
        self.assertEqual(kwargs["headers"]["APCA-API-SECRET-KEY"], "local-test-secret")
        self.assertNotIn("local-test-secret", repr(data))
        for cache in Path(self.directory.name).glob("*.json"):
            self.assertNotIn("local-test-secret", cache.read_text())

    def test_credentials_precedence_and_no_pair_mixing(self):
        path = self.local_file()
        environment = self.adapter([self.page()], credentials_file=path)
        environment.get_bars("SPY", "2024-01-02", "2024-01-02")
        self.assertEqual(environment.session.calls[0][1]["headers"]["APCA-API-KEY-ID"], "offline-test-key")
        explicit = self.adapter([self.page()], credentials_file=path,
                                key="explicit-test-key", secret="explicit-test-secret", feed="sip")
        # Separate date/feed keeps this call independent of an earlier cached result.
        explicit.get_bars("SPY", "2024-01-02", "2024-01-02")
        self.assertEqual(explicit.session.calls[0][1]["headers"]["APCA-API-KEY-ID"], "explicit-test-key")
        with patch.dict(os.environ, {"APCA_API_KEY_ID": "partial-test-key"}, clear=True):
            partial = self.adapter([], credentials_file=path, feed="otc")
            with self.assertRaises(AlpacaDataError):
                partial.get_bars("SPY", "2024-01-02", "2024-01-02")
            self.assertFalse(partial.session.calls)
        partial = self.adapter([], credentials_file=path, key="only-one", feed="otc")
        with self.assertRaisesRegex(AlpacaDataError, "both key and secret"):
            partial.get_bars("SPY", "2024-01-02", "2024-01-02")

    def test_local_secrets_not_in_public_config_and_malformed_yaml_redacted(self):
        from backtest import load_config
        path = self.local_file()
        config = load_config({"data": {"alpaca": {"credentials_file": str(path)}}})
        snapshot = json.dumps(config.to_dict())
        self.assertNotIn("local-test-secret", snapshot)
        self.assertNotIn("local-test-key", snapshot)
        bad = self.local_file("secret: [local-test-secret\n")
        with self.assertRaises(AlpacaDataError) as error:
            self.adapter([], credentials_file=bad)
        self.assertNotIn("local-test-secret", str(error.exception))

    def test_endpoints_do_not_allow_secret_redirect(self):
        for kwargs in [{"data_endpoint": "https://example.com/v2"},
                       {"endpoint": "https://example.com/v2"}]:
            with self.assertRaises(ValueError):
                self.adapter([], **kwargs)


if __name__ == "__main__":
    unittest.main()
