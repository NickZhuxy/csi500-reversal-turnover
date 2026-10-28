"""Synthetic, offline panel that mimics BaoStock conventions (suspensions, limits, dividends)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from revturn.config import load_config
from revturn.panel import Panel, adjusted_index, float_cap, limit_flags, limit_pct


def make_panel(n_dates: int = 520, n_codes: int = 60, seed: int = 0) -> Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-02", periods=n_dates)
    prefixes = ["sh.600", "sz.000", "sz.300", "sh.688"]
    codes = pd.Index([f"{prefixes[i % 4]}{i:03d}" for i in range(n_codes)])
    ret = rng.normal(0.0003, 0.02, (n_dates, n_codes))
    close = 10 * np.exp(np.cumsum(ret, axis=0))
    close = np.round(close, 2)
    preclose = np.vstack([close[0] / (1 + ret[0]), close[:-1]])
    div = rng.random((n_dates, n_codes)) < 0.004  # ex-dividend days
    preclose = np.where(div, preclose * 0.97, preclose)
    open_ = np.round(preclose * (1 + rng.normal(0, 0.005, close.shape)), 2)
    shares = rng.uniform(2e8, 2e9, n_codes)
    volume = shares * rng.uniform(0.002, 0.05, close.shape)
    susp = rng.random(close.shape) < 0.01
    tradestatus = np.where(susp, 0.0, 1.0)
    # Suspended bars: OHLC = previous close, volume 0, turnover empty (BaoStock convention).
    for arr in (open_, close):
        arr[susp] = preclose[susp]
    volume = np.where(susp, 0.0, volume)
    turn = np.where(susp, np.nan, volume / shares * 100)
    frame = lambda a: pd.DataFrame(a, index=dates, columns=codes)  # noqa: E731
    f = {
        "open": frame(open_),
        "high": frame(np.maximum(open_, close) * 1.01),
        "low": frame(np.minimum(open_, close) * 0.99),
        "close": frame(close),
        "preclose": frame(preclose),
        "volume": frame(volume),
        "amount": frame(volume * close),
        "turn": frame(turn),
        "tradestatus": frame(tradestatus),
        "is_st": frame(np.zeros(close.shape)),
    }
    f["has_row"] = f["close"].notna()
    f["adj_close"] = adjusted_index(f["close"], f["preclose"])
    f["adj_open"] = f["adj_close"] * f["open"] / f["close"]
    pct = limit_pct(codes, dates, f["is_st"])
    f["open_limit_up"], f["open_limit_down"] = limit_flags(f["open"], f["preclose"], pct)
    f["member"] = frame(np.ones(close.shape, dtype=bool))
    f["listed_days"] = frame(np.full(close.shape, 500.0))
    f["float_cap"] = float_cap(f["close"], f["volume"], f["turn"])
    groups = np.array(["11", "22", "27", "37", "48", "64"])
    f["industry"] = pd.DataFrame(
        np.tile(groups[np.arange(n_codes) % 6], (n_dates, 1)), index=dates, columns=codes
    ).astype(object)
    bench = pd.DataFrame(index=dates)
    bench["tr_close"] = 1000 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n_dates)))
    bench["px_open"] = bench["tr_close"]
    bench["px_close"] = bench["tr_close"] * 0.999
    bench["fut_ret"] = rng.normal(0, 0.012, n_dates)
    return Panel(fields=f, benchmarks=bench, meta={"synthetic": True})


@pytest.fixture
def panel() -> Panel:
    return make_panel()


@pytest.fixture
def cfg_path(tmp_path):
    cfg = {
        "data_dir": str(tmp_path / "data"),
        "runs_dir": str(tmp_path / "runs"),
        "splits": {
            "dev": {"start": "2017-03-01", "end": "2018-06-29", "label_end": "2018-06-29"},
            "oos_a": {"start": "2018-07-02", "end": "2018-12-31", "label_end": "2018-12-31"},
        },
        "oos_splits": ["oos_a"],
        "min_names": 20,
        "min_industry_size": 3,
        "comparable_subperiod": ["2018-01-01", "2018-06-29"],
        "stale_membership_window": ["2017-11-01", "2017-11-30"],
    }
    p = tmp_path / "configs" / "test.json"
    p.parent.mkdir()
    p.write_text(json.dumps(cfg))
    return p


@pytest.fixture
def cfg(cfg_path):
    return load_config(cfg_path)
