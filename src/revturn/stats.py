"""Rank IC, Newey-West (HAC) t-statistics, and Benjamini-Hochberg q-values (protocol section 7)."""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
from scipy import stats as sps


def rank_ic(
    signal: pd.DataFrame, label: pd.DataFrame, mask: pd.DataFrame, min_names: int = 100
) -> pd.Series:
    """Daily Spearman correlation over names where mask holds and both values exist.

    Vectorized: rank each row on the common set, then a row-wise Pearson correlation of ranks.
    """
    ok = mask & signal.notna() & label.notna()
    rs = signal.where(ok).rank(axis=1).to_numpy(float)
    rl = label.where(ok).rank(axis=1).to_numpy(float)
    n = ok.sum(axis=1).to_numpy()
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        rs = rs - np.nanmean(rs, axis=1, keepdims=True)
        rl = rl - np.nanmean(rl, axis=1, keepdims=True)
        num = np.nansum(rs * rl, axis=1)
        den = np.sqrt(np.nansum(rs**2, axis=1) * np.nansum(rl**2, axis=1))
        ic = num / den
    ic[n < min_names] = np.nan
    return pd.Series(ic, index=signal.index, name="ic")


def newey_west_se(x: np.ndarray, lags: int) -> float:
    """Standard error of the mean with Bartlett-weighted autocovariances up to `lags`."""
    x = np.asarray(x, float)
    t = len(x)
    e = x - x.mean()
    s = e @ e / t
    for j in range(1, min(lags, t - 1) + 1):
        w = 1.0 - j / (lags + 1.0)
        s += 2.0 * w * (e[j:] @ e[:-j]) / t
    return math.sqrt(max(s, 0.0) / t)


def hac_lags(h: int) -> int:
    return max(5, 2 * h)


def hac_test(x: pd.Series, lags: int) -> tuple[float, float]:
    """(t, two-sided normal p) for H0: mean = 0. NaN when too few observations."""
    v = x.dropna().to_numpy(float)
    if len(v) < max(20, lags + 2):
        return float("nan"), float("nan")
    se = newey_west_se(v, lags)
    t = v.mean() / se if se > 0 else float("nan")
    return float(t), float(2 * sps.norm.sf(abs(t)))


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (step-up, monotone), NaNs kept."""
    p = np.asarray(p, float)
    q = np.full_like(p, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    m = len(pv)
    if m == 0:
        return q
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[ok] = out
    return q


def ic_summary(ic: pd.Series, h: int) -> dict:
    v = ic.dropna()
    lags = hac_lags(h)
    t, p = hac_test(v, lags)
    sd = float(v.std(ddof=1)) if len(v) > 1 else float("nan")
    mean = float(v.mean()) if len(v) else float("nan")
    return {
        "n_days": int(len(v)),
        "mean_ic": mean,
        "std_ic": sd,
        "icir": mean / sd if sd and sd > 0 else float("nan"),
        "positive_ratio": float((v > 0).mean()) if len(v) else float("nan"),
        "hac_lags": lags,
        "hac_t": t,
        "p_value": p,
    }


def per_year(ic: pd.Series, h: int) -> pd.DataFrame:
    rows = []
    for year, v in ic.dropna().groupby(ic.dropna().index.year):
        t, _ = hac_test(v, hac_lags(h))
        rows.append({"year": int(year), "n_days": len(v), "mean_ic": v.mean(), "hac_t": t})
    return pd.DataFrame(rows)


def mean_cross_corr(a: pd.DataFrame, b: pd.DataFrame, mask: pd.DataFrame) -> float:
    """Average daily Spearman correlation between two signals on a common mask."""
    return float(rank_ic(a, b, mask, min_names=30).mean())
