"""Read-only Alpaca bars. Private credentials stay out of public config/cache."""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import requests
import yaml

from .configuration import UNSET, load_config
from .data import MarketDataSource, date_range, validate_bars


class AlpacaDataError(RuntimeError):
    pass


class AlpacaDataSource(MarketDataSource):
    ENDPOINT = "https://data.alpaca.markets/v2/stocks/bars"

    def __init__(self, *, config=None, cache_dir=None, feed=None, adjustment=None,
                 timeout=None, page_limit=None, max_pages=None, session=None,
                 endpoint=UNSET, data_endpoint=UNSET, credentials_file=UNSET,
                 key=UNSET, secret=UNSET):
        settings = load_config(config).section("data")["alpaca"]
        root = Path(__file__).resolve().parents[1]
        private_path = settings["credentials_file"] if credentials_file is UNSET else credentials_file
        private = {}
        if private_path is not None:
            path = Path(private_path)
            path = path if path.is_absolute() else root / path
            if path.exists():
                try:
                    private = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (OSError, yaml.YAMLError):
                    raise AlpacaDataError("cannot read Alpaca local credentials YAML") from None
                if not isinstance(private, dict) or set(private) - {"endpoint", "data_endpoint", "key", "secret"}:
                    raise AlpacaDataError("invalid Alpaca local credentials fields")
                for field in ("key", "secret"):
                    if private.get(field) is not None and (not isinstance(private[field], str) or not private[field].strip()):
                        raise AlpacaDataError("Alpaca credential values must be nonempty strings")
        self.trading_endpoint = (private.get("endpoint", settings["endpoint"])
                                 if endpoint is UNSET else endpoint)
        if self.trading_endpoint not in {"https://paper-api.alpaca.markets/v2", "https://api.alpaca.markets/v2"}:
            raise ValueError("Alpaca trading endpoint must be an official HTTPS endpoint")
        data_origin = (private.get("data_endpoint", settings["data_endpoint"])
                       if data_endpoint is UNSET else data_endpoint)
        if data_origin != "https://data.alpaca.markets/v2":
            raise ValueError("Alpaca data endpoint must be https://data.alpaca.markets/v2")
        self.data_endpoint = data_origin + "/stocks/bars"
        self._file_key, self._file_secret = private.get("key"), private.get("secret")
        self._explicit_key, self._explicit_secret = key, secret
        for value in (key, secret):
            if value is not UNSET and value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("explicit Alpaca credentials must be nonempty strings or None")
        self.feed = settings["feed"] if feed is None else feed
        self.adjustment = settings["adjustment"] if adjustment is None else adjustment
        self.timeout = settings["timeout"] if timeout is None else timeout
        self.page_limit = settings["page_limit"] if page_limit is None else page_limit
        self.max_pages = settings["max_pages"] if max_pages is None else max_pages
        if self.feed not in {"iex", "sip", "otc", "boats"}:
            raise ValueError("unsupported Alpaca feed")
        if self.adjustment not in {"raw", "split", "dividend", "spin-off", "all"}:
            raise ValueError("unsupported Alpaca adjustment")
        if isinstance(self.page_limit, bool) or not isinstance(self.page_limit, int) or not 1 <= self.page_limit <= 10000:
            raise ValueError("page_limit must be an integer from 1 to 10000")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int) or self.max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not 0 < self.timeout < float("inf"):
            raise ValueError("timeout must be finite and positive")
        path = Path(settings["cache_dir"] if cache_dir is None else cache_dir)
        self.cache_dir = path if path.is_absolute() else Path(__file__).resolve().parents[1] / path
        self.key_env, self.secret_env = settings["key_env"], settings["secret_env"]
        self.session = session if session is not None else requests.Session()

    def get_bars(self, symbol, start, end):
        if not isinstance(symbol, str) or not symbol or symbol.endswith((".SS", ".SZ")):
            raise ValueError("Alpaca stocks API supports US symbols, not Chinese .SS/.SZ ETFs")
        start, end = date_range(start, end)
        request = dict(symbols=symbol, timeframe="1Day", feed=self.feed,
                       adjustment=self.adjustment, sort="asc", limit=self.page_limit,
                       start=start.tz_localize("America/New_York").isoformat(),
                       end=(end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)).tz_localize("America/New_York").isoformat())
        identity = {k: v for k, v in request.items() if k != "limit"}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / f"{digest}.csv"
        if path.exists():
            cached = validate_bars(pd.read_csv(path, index_col=0, parse_dates=True))
            if cached.empty:
                raise AlpacaDataError("cached Alpaca response has no bars")
            return cached.loc[start:end].copy()
        # Select a complete credential pair. Never combine one environment key
        # with a secret from a different local-file pair.
        env_key, env_secret = os.environ.get(self.key_env), os.environ.get(self.secret_env)
        if (self._explicit_key is UNSET) != (self._explicit_secret is UNSET):
            raise AlpacaDataError("pass both key and secret together")
        if self._explicit_key is not UNSET:
            key, secret = self._explicit_key, self._explicit_secret
        elif env_key or env_secret:
            key, secret = env_key, env_secret
        else:
            key, secret = self._file_key, self._file_secret
        if not key or not secret:
            raise AlpacaDataError(f"set both {self.key_env} and {self.secret_env}, or provide a complete local YAML pair; browser login is not API authentication")
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        rows, seen = [], set()
        for _ in range(self.max_pages):
            try:
                response = self.session.get(self.data_endpoint, params=request.copy(), headers=headers,
                                            timeout=self.timeout, allow_redirects=False)
            except requests.RequestException:
                raise AlpacaDataError("Alpaca data request failed; check network connectivity") from None
            if response.status_code != 200:
                meanings = {401: "invalid API credentials", 403: "feed/account permission denied",
                            429: "rate limited; retry later"}
                raise AlpacaDataError(f"Alpaca HTTP {response.status_code}: {meanings.get(response.status_code, 'data request rejected')}")
            try:
                payload = response.json()
                rows.extend(payload.get("bars", {}).get(symbol, []))
                token = payload.get("next_page_token")
            except (ValueError, TypeError, AttributeError):
                raise AlpacaDataError("invalid Alpaca response schema") from None
            if not token:
                break
            if token in seen:
                raise AlpacaDataError("Alpaca pagination repeated a page token")
            seen.add(token)
            request["page_token"] = token
        else:
            raise AlpacaDataError("Alpaca pagination exceeded max_pages; incomplete data was not cached")
        if not rows:
            raise AlpacaDataError(f"Alpaca returned no bars for {symbol}; check symbol, range and feed")
        try:
            frame = pd.DataFrame(rows).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            frame.index = pd.to_datetime(frame.pop("t"), utc=True).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
            frame = validate_bars(frame).loc[start:end]
        except (ValueError, KeyError, TypeError):
            raise AlpacaDataError("invalid daily bars in Alpaca response") from None
        if frame.empty:
            raise AlpacaDataError("Alpaca response has no bars inside requested dates")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
        return frame.copy()
