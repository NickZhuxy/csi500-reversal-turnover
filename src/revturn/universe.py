"""Eligibility at the signal close t, and tradability at the next open (protocol section 4)."""

from __future__ import annotations

import pandas as pd

from .config import Config
from .panel import Panel


def traded(panel: Panel) -> pd.DataFrame:
    return panel["tradestatus"] == 1


def eligible(panel: Panel, cfg: Config) -> pd.DataFrame:
    """Uses only information available at the close of t."""
    tr = traded(panel)
    recent = tr.astype(float).rolling(cfg.lookback, min_periods=cfg.lookback).sum()
    return (
        panel["member"]
        & (panel["is_st"] == 0)
        & tr
        & (panel["listed_days"] >= cfg.min_listed_days)
        & (recent >= cfg.min_traded_days)
    )


def buyable_at_open(panel: Panel) -> pd.DataFrame:
    """Same-day flag: a buy at this session's open can fill (traded, not opened limit-up)."""
    return traded(panel) & ~panel["open_limit_up"]


def sellable_at_open(panel: Panel) -> pd.DataFrame:
    """Same-day flag: a sell at this session's open can fill (traded, not opened limit-down)."""
    return traded(panel) & ~panel["open_limit_down"]


def entry_tradable(panel: Panel) -> pd.DataFrame:
    """Row t: the name can be traded in either direction at the t+1 open.

    This is the only forward-shifted mask in the project. It describes the execution session and
    is used for evaluation filters, never as a signal input.
    """
    both = traded(panel) & ~panel["open_limit_up"] & ~panel["open_limit_down"]
    return both.shift(-1, fill_value=False).astype(bool)
