"""Look-ahead guards: nothing known after the close of t may change a value dated t."""

import copy

import numpy as np
import pandas as pd

from revturn.factors import processed_signals
from revturn.labels import forward_returns, label_end_ok
from revturn.panel import Panel, adjusted_index
from revturn.portfolio import quintile_study, schedule, simulate_long
from revturn.universe import buyable_at_open, eligible, entry_tradable, sellable_at_open

RAW = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn"]


def truncate(p: Panel, end: pd.Timestamp) -> Panel:
    f = {k: v.loc[:end] for k, v in p.fields.items()}
    return Panel(fields=f, benchmarks=p.benchmarks.loc[:end], meta=p.meta)


def perturb_after(p: Panel, t: pd.Timestamp, seed: int = 1) -> Panel:
    """Scramble every raw input strictly after t, then rebuild the derived fields."""
    q = copy.deepcopy(p)
    rng = np.random.default_rng(seed)
    later = q.dates > t
    for k in RAW:
        noise = rng.uniform(0.5, 1.5, q[k].loc[later].shape)
        q.fields[k].loc[later] = q[k].loc[later].to_numpy() * noise
    q.fields["tradestatus"].loc[later] = rng.integers(0, 2, q["tradestatus"].loc[later].shape)
    q.fields["adj_close"] = adjusted_index(q["close"], q["preclose"])
    q.fields["adj_open"] = q["adj_close"] * q["open"] / q["close"]
    return q


def all_signals(p, cfg):
    return processed_signals(p, eligible(p, cfg), cfg)


def test_signals_are_prefix_invariant(panel, cfg):
    t = panel.dates[300]
    full = all_signals(panel, cfg)
    cut = all_signals(truncate(panel, t), cfg)
    assert len(full) == 10
    for key in full:
        a, b = full[key].loc[:t], cut[key]
        assert np.allclose(a.to_numpy(), b.to_numpy(), equal_nan=True), key


def test_future_data_cannot_move_signals_or_eligibility(panel, cfg):
    t = panel.dates[300]
    q = perturb_after(panel, t)
    assert eligible(panel, cfg).loc[:t].equals(eligible(q, cfg).loc[:t])
    a, b = all_signals(panel, cfg), all_signals(q, cfg)
    for key in a:
        assert np.allclose(a[key].loc[:t].to_numpy(), b[key].loc[:t].to_numpy(), equal_nan=True), (
            key
        )


def test_label_starts_at_next_open(panel):
    h, i = 5, 200
    lab = forward_returns(panel, h)
    ao = panel["adj_open"]
    expect = ao.iloc[i + 1 + h] / ao.iloc[i + 1] - 1
    assert np.allclose(lab.iloc[i].to_numpy(), expect.to_numpy(), equal_nan=True)


def test_close_at_t_moves_signal_but_not_label(panel, cfg):
    i = 250
    t = panel.dates[i]
    q = copy.deepcopy(panel)
    bump = np.random.default_rng(7).uniform(0.9, 1.1, len(panel.codes))
    q.fields["close"].iloc[i] *= bump  # the close of t changes; t+1 reference price does not
    q.fields["adj_close"] = adjusted_index(q["close"], q["preclose"])
    q.fields["adj_open"] = q["adj_close"] * q["open"] / q["close"]
    for h in (1, 5, 20):
        assert np.allclose(
            forward_returns(panel, h).loc[t].to_numpy(),
            forward_returns(q, h).loc[t].to_numpy(),
            equal_nan=True,
        )
    a = all_signals(panel, cfg)[("REV20", "raw")].loc[t]
    b = all_signals(q, cfg)[("REV20", "raw")].loc[t]
    assert not np.allclose(a.to_numpy(), b.to_numpy(), equal_nan=True)


def test_label_end_purges_windows_that_cross_the_split():
    dates = pd.bdate_range("2024-12-02", "2025-01-31")
    ok = label_end_ok(dates, 5, "2024-12-31")
    last_kept = ok[ok].index.max()
    assert dates.get_loc(last_kept) + 1 + 5 == dates.get_loc(pd.Timestamp("2024-12-31"))


def test_entry_filter_is_the_only_forward_mask(panel):
    m = entry_tradable(panel)
    i = 100
    nxt = (
        (panel["tradestatus"].iloc[i + 1] == 1)
        & ~panel["open_limit_up"].iloc[i + 1]
        & ~panel["open_limit_down"].iloc[i + 1]
    )
    assert m.iloc[i].equals(nxt)


def test_portfolios_ignore_prices_after_the_split(panel, cfg):
    sch = schedule(panel.dates, "2017-03-01", "2018-06-29", "2018-06-29")
    end = panel.dates[sch.end_idx]
    q = perturb_after(panel, end)
    sig = all_signals(panel, cfg)[("RxT", "neutral")]
    elig = eligible(panel, cfg)
    mask = elig & entry_tradable(panel)

    def quint(p):
        from revturn.labels import exit_prices

        return quintile_study(sig, mask, p["adj_open"], exit_prices(p), p["adj_close"], sch)

    pd.testing.assert_frame_equal(quint(panel), quint(q))
    targets = {
        i: list(sig.loc[t].dropna().nlargest(10).index)
        for t, i in zip(sch.signal_dates, sch.exec_idx)
    }

    def sim(p):
        adv = p["amount"].rolling(20, min_periods=10).mean().shift(1)
        return simulate_long(
            targets,
            p["adj_open"],
            p["adj_close"],
            buyable_at_open(p),
            sellable_at_open(p),
            adv,
            sch.exec_idx[0],
            sch.end_idx,
            0.002,
        ).daily

    pd.testing.assert_series_equal(sim(panel), sim(q))
