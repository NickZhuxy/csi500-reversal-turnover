"""The five pre-registered signals and their cross-sectional processing (protocol section 5).

Every function maps information up to the close of t to a value at t. Cross-sectional statistics
use eligible names on that date only.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .config import Config
from .panel import Panel

SIGNALS = ("REV20", "LOWTURN20", "RxT", "RxT_RANK", "ADD")
VERSIONS = ("raw", "neutral")


def inputs(panel: Panel, lookback: int = 20, min_traded: int = 15) -> dict[str, pd.DataFrame]:
    """r20 over `lookback` exchange sessions; turn20 = mean turnover over traded days."""
    adj = panel["adj_close"]
    r = adj / adj.shift(lookback) - 1.0
    turn = panel["turn"].where(panel["tradestatus"] == 1)
    t20 = turn.rolling(lookback, min_periods=min_traded).mean()
    return {"r20": r, "turn20": t20}


def winsorize_mad(x: pd.DataFrame, k: float = 5.0) -> pd.DataFrame:
    """Clip each row at median +/- k * MAD, MAD = median(|x - median|), NaNs ignored."""
    v = x.to_numpy(dtype=float)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows before the sample
        med = np.nanmedian(v, axis=1, keepdims=True)
        mad = np.nanmedian(np.abs(v - med), axis=1, keepdims=True)
    out = np.clip(v, med - k * mad, med + k * mad)
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def zscore(x: pd.DataFrame) -> pd.DataFrame:
    v = x.to_numpy(dtype=float)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mu = np.nanmean(v, axis=1, keepdims=True)
        sd = np.nanstd(v, axis=1, keepdims=True)
        out = (v - mu) / np.where(sd > 0, sd, np.nan)
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def rank_pct(x: pd.DataFrame) -> pd.DataFrame:
    return x.rank(axis=1, pct=True)


def neutralize(
    z: pd.DataFrame, industry: pd.DataFrame, log_cap: pd.DataFrame, min_group: int = 5
) -> pd.DataFrame:
    """Residual of z on industry dummies + z(log cap) per date, then z-scored again.

    Industries with fewer than `min_group` names that day are pooled into one group. Names with no
    industry join that pooled group; names with no cap are dropped (NaN).
    """
    zc = zscore(log_cap.where(z.notna()))
    out = np.full(z.shape, np.nan)
    zv, cv, iv = z.to_numpy(float), zc.to_numpy(float), industry.to_numpy(object)
    for i in range(z.shape[0]):
        ok = np.isfinite(zv[i]) & np.isfinite(cv[i])
        if ok.sum() < 30:
            continue
        # Integer-coded one-hot with numpy: 2.4x faster than pandas get_dummies per row and
        # identical to 1e-15 on the real panel (docs/DATA_PROFILE.md).
        g = iv[i, ok]
        g = np.where(pd.isna(g), "__pool__", g).astype(str)
        labels, inv = np.unique(g, return_inverse=True)
        small = np.bincount(inv)[inv] < min_group
        if small.any():
            labels, inv = np.unique(np.where(small, "__pool__", g), return_inverse=True)
        X = np.zeros((inv.size, labels.size + 1))
        X[np.arange(inv.size), inv] = 1.0
        X[:, -1] = cv[i, ok]
        beta, *_ = np.linalg.lstsq(X, zv[i, ok], rcond=None)
        out[i, ok] = zv[i, ok] - X @ beta
    return zscore(pd.DataFrame(out, index=z.index, columns=z.columns))


def raw_signals(panel: Panel, elig: pd.DataFrame, cfg: Config) -> dict[str, pd.DataFrame]:
    """Unprocessed values on eligible names (NaN elsewhere), higher = better.

    ADD is not here: it is the sum of the processed REV20 and LOWTURN20 parts.
    """
    x = inputs(panel, cfg.lookback, cfg.min_traded_days)
    r = x["r20"].where(elig)
    t = x["turn20"].where(elig)
    ok = r.notna() & t.notna()
    r, t = r.where(ok), t.where(ok)
    med = r.median(axis=1)
    return {
        "REV20": -r,
        "LOWTURN20": -t,
        "RxT": -(r.sub(med, axis=0)) * t,
        "RxT_RANK": (rank_pct(-r) - 0.5) * rank_pct(t),
    }


def processed_signals(
    panel: Panel, elig: pd.DataFrame, cfg: Config
) -> dict[tuple[str, str], pd.DataFrame]:
    """All 10 series: winsorize -> z-score (raw); then industry + size residual (neutral)."""
    raw = raw_signals(panel, elig, cfg)

    def prep(x: pd.DataFrame) -> pd.DataFrame:
        return zscore(winsorize_mad(x, cfg.mad_k))

    out: dict[tuple[str, str], pd.DataFrame] = {}
    for name in ("REV20", "LOWTURN20", "RxT", "RxT_RANK"):
        out[(name, "raw")] = prep(raw[name])
    out[("ADD", "raw")] = zscore(out[("REV20", "raw")] + out[("LOWTURN20", "raw")])
    log_cap = np.log(panel["float_cap"])
    for name in SIGNALS:
        out[(name, "neutral")] = neutralize(
            out[(name, "raw")], panel["industry"], log_cap, cfg.min_industry_size
        )
    return out
