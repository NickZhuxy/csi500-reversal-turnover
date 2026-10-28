"""Forward returns: enter at the t+1 open, exit at the t+1+h open (protocol section 6)."""

from __future__ import annotations

import pandas as pd

from .panel import Panel


def after_last_bar(panel: Panel) -> pd.DataFrame:
    """True on sessions after a code's final downloaded bar (delisted, or past its window)."""
    return panel["adj_close"].isna() & panel["adj_close"].bfill().isna()


def exit_prices(panel: Panel, max_fill: int = 25) -> pd.DataFrame:
    """Adjusted open, or the last adjusted close after a code's final bar (delisting).

    Suspended sessions already carry the previous close as their open in BaoStock data.
    """
    last = panel["adj_close"].ffill(limit=max_fill)
    return panel["adj_open"].where(~after_last_bar(panel), last)


def forward_returns(panel: Panel, h: int) -> pd.DataFrame:
    entry = panel["adj_open"].shift(-1)
    exit_ = exit_prices(panel).shift(-(1 + h))
    return exit_ / entry - 1.0


def label_end_ok(dates: pd.DatetimeIndex, h: int, label_end: str) -> pd.Series:
    """True for signal dates whose label window (t+1+h) ends on or before `label_end`."""
    pos = pd.Series(range(len(dates)), index=dates)
    last = dates.searchsorted(pd.Timestamp(label_end), side="right") - 1
    return pos + 1 + h <= last
