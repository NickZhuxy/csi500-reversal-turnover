"""Monthly quintile portfolios and a daily long-only simulator with A-share constraints.

Timing (protocol section 8): signal at the close of the last session of month k, trade at the
next open, hold to the next rebalance's trade open. The final holding of a split is marked at the
close of the split's label end, so no price after the split is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Schedule:
    signal_dates: list[pd.Timestamp]
    exec_idx: list[int]  # calendar row of each trade open
    end_idx: int  # calendar row of the split's label end (marked at its close)


def schedule(dates: pd.DatetimeIndex, start: str, end: str, label_end: str) -> Schedule:
    """Month-end signal dates in [start, end] whose next-session trade falls by label_end."""
    d = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    month_end = pd.Series(d, index=d).groupby([d.year, d.month]).max().tolist()
    end_idx = int(dates.searchsorted(pd.Timestamp(label_end), side="right") - 1)
    sig, ex = [], []
    for t in month_end:
        i = int(dates.get_loc(t)) + 1
        if i <= end_idx:
            sig.append(t)
            ex.append(i)
    return Schedule(sig, ex, end_idx)


def quantile_buckets(signal_row: pd.Series, n_q: int) -> pd.Series:
    """Bucket 1 (lowest) .. n_q (highest) by percentile rank; NaN signals dropped."""
    s = signal_row.dropna()
    return np.ceil(s.rank(pct=True) * n_q).clip(1, n_q).astype(int)


def quintile_study(
    signal: pd.DataFrame,
    mask: pd.DataFrame,
    entry_px: pd.DataFrame,
    exit_px: pd.DataFrame,
    close_px: pd.DataFrame,
    sch: Schedule,
    n_q: int = 5,
) -> pd.DataFrame:
    """Per rebalance and bucket: gross period return and two-way turnover of an equal-weight book.

    Entry at the trade open; exit at the next trade open, or at the label-end close for the last.
    """
    rows = []
    prev_drift: dict[int, pd.Series] = {}
    for k, (t, i) in enumerate(zip(sch.signal_dates, sch.exec_idx)):
        last = k + 1 == len(sch.exec_idx)
        j = sch.end_idx if last else sch.exec_idx[k + 1]
        # The last holding is marked at the label-end close; a code whose bars ended earlier
        # keeps its final close, as exit_px does for earlier periods.
        exit_row = close_px.ffill(limit=25).iloc[j] if last else exit_px.iloc[j]
        buckets = quantile_buckets(signal.loc[t].where(mask.loc[t]), n_q)
        for q in range(1, n_q + 1):
            names = buckets.index[buckets == q]
            r = (exit_row[names] / entry_px.iloc[i][names] - 1.0).dropna()
            n_formed = len(names)
            w = pd.Series(1.0 / len(r), index=r.index) if len(r) else pd.Series(dtype=float)
            old = prev_drift.get(q, pd.Series(dtype=float))
            turnover = w.sub(old, fill_value=0.0).abs().sum()
            drift = w * (1 + r)
            prev_drift[q] = drift / drift.sum() if len(r) else drift
            rows.append(
                {
                    "signal_date": t,
                    "bucket": q,
                    "n": len(r),
                    "n_formed": n_formed,
                    "ret": float(r.mean()) if len(r) else np.nan,
                    "turnover": float(turnover),
                }
            )
    return pd.DataFrame(rows)


@dataclass
class SimResult:
    daily: pd.Series  # close-to-close NAV returns
    turnover: pd.Series  # two-way traded value / NAV, per rebalance month
    n_held: pd.Series
    blocked_buys: int = 0
    blocked_sells: int = 0
    forced_exits: int = 0
    capacity_per_trade: list[float] = field(default_factory=list)


def simulate_long(
    targets: dict[int, list[str]],
    adj_open: pd.DataFrame,
    adj_close: pd.DataFrame,
    buyable: pd.DataFrame,
    sellable: pd.DataFrame,
    adv: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    cost: float,
    participation: float = 0.05,
) -> SimResult:
    """Equal-weight long book, traded at opens, with blocked trades carried forward.

    `targets` maps a trade-open calendar row to its target names. A buy fails when the name is
    suspended or opens limit-up; a sell fails when it is suspended or opens limit-down, and is
    retried at every later open. Cash earns nothing. `cost` is one-way, on traded value.
    """
    codes = adj_open.columns
    col = {c: n for n, c in enumerate(codes)}
    o, c = adj_open.to_numpy(float), adj_close.to_numpy(float)
    buy_ok, sell_ok = buyable.to_numpy(bool), sellable.to_numpy(bool)
    advv = adv.to_numpy(float)
    has_future = np.flip(np.cumsum(np.flip(np.isfinite(c), axis=0), axis=0), axis=0) > 0

    cash, nav_prev = 1.0, 1.0
    held: dict[int, float] = {}
    target: set[int] = set()
    out_r, out_n, turn = {}, {}, {}
    res = SimResult(
        daily=pd.Series(dtype=float), turnover=pd.Series(dtype=float), n_held=pd.Series(dtype=float)
    )
    month_key = None
    for i in range(start_idx, end_idx + 1):
        if i > start_idx:  # overnight move
            for j in list(held):
                if np.isfinite(o[i, j]) and np.isfinite(c[i - 1, j]):
                    held[j] *= o[i, j] / c[i - 1, j]
        traded = 0.0
        if i in targets:
            target = {col[n] for n in targets[i] if n in col}
            month_key = adj_open.index[i]
        for j in list(held):  # forced exits after a code's last bar (delisting)
            if not np.isfinite(c[i, j]) and not has_future[i, j]:
                cash += held[j] * (1 - cost)
                traded += held.pop(j)
                res.forced_exits += 1
        for j in [j for j in held if j not in target]:  # exits, retried daily
            if sell_ok[i, j]:
                cash += held[j] * (1 - cost)
                traded += held.pop(j)
            elif i in targets:
                res.blocked_sells += 1
        if i in targets:
            nav_open = cash + sum(held.values())
            stuck = sum(v for j, v in held.items() if j not in target)
            names = [j for j in target if j in held or buy_ok[i, j]]
            res.blocked_buys += len(target) - len(names)
            if names:
                each = (nav_open - stuck) / len(names)
                for j in names:
                    delta = each - held.get(j, 0.0)
                    if (delta > 0 and not buy_ok[i, j]) or (delta < 0 and not sell_ok[i, j]):
                        continue
                    held[j] = held.get(j, 0.0) + delta
                    cash -= delta + cost * abs(delta)
                    traded += abs(delta)
                    if delta > 0 and advv[i, j] > 0:
                        res.capacity_per_trade.append(
                            participation * advv[i, j] / (delta / nav_open)
                        )
        for j in list(held):  # intraday move
            if np.isfinite(c[i, j]) and np.isfinite(o[i, j]) and o[i, j] > 0:
                held[j] *= c[i, j] / o[i, j]
        nav = cash + sum(held.values())
        d = adj_open.index[i]
        out_r[d] = nav / nav_prev - 1.0
        out_n[d] = len(held)
        if month_key is not None:
            turn[month_key] = turn.get(month_key, 0.0) + traded / max(nav_prev, 1e-12)
        nav_prev = nav
    res.daily = pd.Series(out_r)
    res.n_held = pd.Series(out_n)
    res.turnover = pd.Series(turn)
    return res


def perf(r: pd.Series, periods: int = 242) -> dict:
    """Annualized summary of a periodic return series (NaNs dropped)."""
    r = r.dropna()
    if len(r) < 2:
        return {"n": len(r)}
    nav = (1 + r).cumprod()
    peak = np.maximum.accumulate(np.concatenate([[1.0], nav.to_numpy()]))
    dd = np.concatenate([[1.0], nav.to_numpy()]) / peak - 1
    sd = r.std(ddof=1)
    return {
        "n": int(len(r)),
        "ann_return": float(nav.iloc[-1] ** (periods / len(r)) - 1),
        "ann_vol": float(sd * np.sqrt(periods)),
        "sharpe": float(r.mean() / sd * np.sqrt(periods)) if sd > 0 else float("nan"),
        "max_drawdown": float(dd.min()),
        "total_return": float(nav.iloc[-1] - 1),
    }


def monthly_hit_rate(daily: pd.Series) -> float:
    m = (1 + daily.dropna()).groupby(daily.dropna().index.to_period("M")).prod() - 1
    return float((m > 0).mean()) if len(m) else float("nan")
