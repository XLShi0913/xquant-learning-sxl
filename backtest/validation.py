"""Top-level validation plans; no prices, strategies, brokers or engine state.

Calendar-based plans return inclusive date bounds. Random K-fold returns exact
row selections, never a misleading min/max envelope around scattered dates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from numbers import Integral
import re

import numpy as np
import pandas as pd

from .configuration import UNSET, configured, load_config


_DAY = pd.Timedelta(days=1)


def _date(value):
    date = pd.Timestamp(value)
    if pd.isna(date) or date.tzinfo is not None or date != date.normalize():
        raise ValueError("dates must be valid timezone-naive calendar dates")
    return date


def _range(start, end):
    start, end = _date(start), _date(end)
    if start > end:
        raise ValueError("start must not be later than end")
    return start, end


def _integer(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _period(value, name):
    match = re.fullmatch(r"([1-9]\d*)\s*([YMD])", value.strip(), re.IGNORECASE) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"{name} must be a positive calendar period, e.g. 2Y, 6M or 126D")
    unit = {"Y": "years", "M": "months", "D": "days"}[match[2].upper()]
    return pd.DateOffset(**{unit: int(match[1])})


def _calendar(dates):
    index = pd.DatetimeIndex(dates)
    if (index.empty or index.hasnans or index.tz is not None or index.has_duplicates
            or not index.is_monotonic_increasing or not index.equals(index.normalize())):
        raise ValueError("dates must be nonempty, unique, increasing, timezone-naive daily dates")
    return tuple(index)


@dataclass(frozen=True)
class DateSplit:
    """One chronological training/validation fold; all four bounds are inclusive."""
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str

    def __post_init__(self):
        a, b = _range(self.train_start, self.train_end)
        c, d = _range(self.validation_start, self.validation_end)
        if b >= c:
            raise ValueError("training must end before validation starts")
        for name, value in zip(self.__dataclass_fields__, (a, b, c, d)):
            object.__setattr__(self, name, value.strftime("%Y-%m-%d"))

    def to_dict(self):
        """Keyword arguments compatible with MarketDataSource.split()."""
        return asdict(self)


@dataclass(frozen=True)
class IndexSplit:
    """Random fold with exact positions into the supplied, unchanged calendar.

    Rows stay in original chronological order within each selection. Training
    may include dates after validation; this is not an executable OOS timeline.
    """
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    dates: tuple[pd.Timestamp, ...] = field(repr=False)

    def __post_init__(self):
        dates = _calendar(self.dates)
        object.__setattr__(self, "dates", dates)
        for name in ("train_indices", "validation_indices"):
            values = tuple(_integer(i, name, 0) for i in getattr(self, name))
            if not values or values != tuple(sorted(set(values))) or values[-1] >= len(dates):
                raise ValueError(f"{name} must contain sorted, unique, nonempty in-range positions")
            object.__setattr__(self, name, values)
        a, b = set(self.train_indices), set(self.validation_indices)
        if a & b or a | b != set(range(len(dates))):
            raise ValueError("random fold must partition every row without overlap")

    @property
    def train_dates(self):
        return tuple(self.dates[i] for i in self.train_indices)

    @property
    def validation_dates(self):
        return tuple(self.dates[i] for i in self.validation_indices)

    @property
    def is_chronological(self):
        return self.train_indices[-1] < self.validation_indices[0]

    def take(self, frame):
        """Independent frame/series copies; reject changed or misaligned dates."""
        if not isinstance(frame, (pd.DataFrame, pd.Series)):
            raise TypeError("take expects a DataFrame or Series with the original date index")
        if _calendar(frame.index) != self.dates:
            raise ValueError("frame dates must exactly match the original split calendar")
        return (frame.iloc[list(self.train_indices)].copy(),
                frame.iloc[list(self.validation_indices)].copy())


def walk_forward_splits(start, end, *, train_period=UNSET, test_period=UNSET,
                        mode=UNSET, step=UNSET, gap_days=UNSET,
                        include_partial=UNSET, config=None) -> tuple[DateSplit, ...]:
    """Rolling/anchored calendar walk-forward; default train 2Y, test/step 6M.

    ``D`` means calendar days, not observations. ``step=None`` follows the test
    period. The last validation window is clipped by default. A short dataset
    with no complete initial training window returns an empty tuple.
    """
    settings = load_config(config).section("validation")["walk_forward"]
    train_period = configured(train_period, settings, "train_period")
    test_period = configured(test_period, settings, "test_period")
    mode = configured(mode, settings, "mode")
    step = configured(step, settings, "step")
    gap_days = _integer(configured(gap_days, settings, "gap_days"), "gap_days", 0)
    partial = _boolean(configured(include_partial, settings, "include_partial"), "include_partial")
    if mode not in {"rolling", "anchored"}:
        raise ValueError("mode must be rolling or anchored")
    train_offset = _period(train_period, "train_period")
    test_offset = _period(test_period, "test_period")
    step_offset = _period(test_period if step is None else step, "step")
    start, end = _range(start, end)
    result = []
    number = 0
    while True:
        # Derive each cursor from the original anchor, avoiding month-end drift.
        cursor = start + number * step_offset
        train_start = start if mode == "anchored" else cursor
        train_end = cursor + train_offset - _DAY
        validation_start = train_end + (gap_days + 1) * _DAY
        if validation_start > end:
            break
        validation_end = validation_start + test_offset - _DAY
        if validation_end > end and not partial:
            break
        result.append(DateSplit(train_start, train_end, validation_start, min(validation_end, end)))
        number += 1
    return tuple(result)


def time_series_cv_splits(start, end, *, n_splits=UNSET, expanding=UNSET,
                          gap_days=UNSET, config=None) -> tuple[DateSplit, ...]:
    """Chronological CV: n+1 calendar blocks, one later test block per fold.

    Expanding mode uses every earlier block; fixed mode uses the immediately
    previous block. All calendar days, including end, belong to a block. The
    optional gap removes the beginning of each test block, not future training.
    """
    settings = load_config(config).section("validation")["time_series_cv"]
    n_splits = _integer(configured(n_splits, settings, "n_splits"), "n_splits", 2)
    expanding = _boolean(configured(expanding, settings, "expanding"), "expanding")
    gap = _integer(configured(gap_days, settings, "gap_days"), "gap_days", 0)
    start, end = _range(start, end)
    days = (end - start).days + 1
    blocks = n_splits + 1
    if days < blocks:
        raise ValueError("date range is too short for n_splits + 1 nonempty blocks")
    edges = [days * i // blocks for i in range(blocks + 1)]
    result = []
    for i in range(n_splits):
        train_start = start if expanding else start + edges[i] * _DAY
        train_end = start + (edges[i + 1] - 1) * _DAY
        validation_start = train_end + (gap + 1) * _DAY
        validation_end = start + (edges[i + 2] - 1) * _DAY
        if validation_start > validation_end:
            raise ValueError("gap_days leaves an empty validation block")
        result.append(DateSplit(train_start, train_end, validation_start, validation_end))
    return tuple(result)


def random_cv_splits(dates, *, n_splits=UNSET, seed=UNSET,
                      config=None) -> tuple[IndexSplit, ...]:
    """Seeded random K-fold by observation, not by calendar-day envelopes.

    Each row validates exactly once. Future observations can occur in training;
    use chronological splitters to assess genuinely forward-looking outcomes.
    This function neither shuffles a price series nor runs/concatenates backtests.
    """
    settings = load_config(config).section("validation")["random_cv"]
    n_splits = _integer(configured(n_splits, settings, "n_splits"), "n_splits", 2)
    seed = _integer(configured(seed, settings, "seed"), "seed", 0)
    dates = _calendar(dates)
    if n_splits > len(dates):
        raise ValueError("n_splits cannot exceed the number of observations")
    rng = np.random.default_rng(seed)  # Local generator; no global RNG mutation.
    validation_blocks = np.array_split(rng.permutation(len(dates)), n_splits)
    universe = set(range(len(dates)))
    return tuple(IndexSplit(tuple(sorted(universe - set(block.tolist()))),
                            tuple(sorted(block.tolist())), dates)
                 for block in validation_blocks)
