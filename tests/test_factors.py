import numpy as np
import pandas as pd

from revturn.factors import (
    inputs,
    neutralize,
    processed_signals,
    raw_signals,
    winsorize_mad,
    zscore,
)
from revturn.universe import eligible


def test_winsorize_clips_at_five_mad():
    row = pd.DataFrame([[1.0, 2.0, 3.0, 4.0, 100.0, np.nan]])
    w = winsorize_mad(row, k=5.0)
    # median 3, MAD 1 -> bounds [-2, 8]
    assert w.iloc[0, 4] == 8.0
    assert w.iloc[0, :4].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert np.isnan(w.iloc[0, 5])


def test_zscore_rows_have_zero_mean_unit_std():
    x = pd.DataFrame(np.random.default_rng(0).normal(3, 2, (5, 50)))
    z = zscore(x)
    assert np.allclose(z.mean(axis=1), 0)
    assert np.allclose(z.std(axis=1, ddof=0), 1)


def test_neutralized_signal_is_orthogonal_to_industry_and_size():
    rng = np.random.default_rng(1)
    n = 200
    ind = pd.DataFrame([rng.choice(["a", "b", "c"], n).tolist() + []], dtype=object)
    cap = pd.DataFrame([rng.lognormal(10, 1, n)])
    z = pd.DataFrame([rng.normal(0, 1, n) + (ind.iloc[0] == "a") * 2 + np.log(cap.iloc[0])])
    out = neutralize(zscore(z), ind, np.log(cap), min_group=5).iloc[0]
    for g in ("a", "b", "c"):
        assert abs(out[ind.iloc[0] == g].mean()) < 1e-8
    assert abs(np.corrcoef(out, np.log(cap.iloc[0]))[0, 1]) < 1e-8


def test_small_industries_are_pooled():
    n = 60
    labels = ["big"] * 55 + ["tiny"] * 2 + [None] * 3
    ind = pd.DataFrame([labels], dtype=object)
    cap = pd.DataFrame([np.linspace(1e9, 5e9, n)])
    z = pd.DataFrame([np.r_[np.zeros(55), [5.0, 5.0], [-5.0, -5.0, -5.0]]])
    out = neutralize(zscore(z), ind, np.log(cap), min_group=5).iloc[0]
    # The pooled group is tiny + unknown; its mean residual is zero, not each name's.
    assert abs(out.iloc[55:].mean()) < 1e-8
    assert out.iloc[55] > 0 > out.iloc[57]


def test_signal_definitions(panel, cfg):
    elig = eligible(panel, cfg)
    raw = raw_signals(panel, elig, cfg)
    x = inputs(panel)
    t = panel.dates[300]
    r, tv = x["r20"].loc[t].where(elig.loc[t]), x["turn20"].loc[t].where(elig.loc[t])
    ok = r.notna() & tv.notna()
    r, tv = r[ok], tv[ok]
    assert np.allclose(raw["REV20"].loc[t].dropna(), -r)
    assert np.allclose(raw["RxT"].loc[t].dropna(), -(r - r.median()) * tv)
    rr = raw["RxT_RANK"].loc[t].dropna()
    assert rr.between(-0.5, 0.5).all()
    proc = processed_signals(panel, elig, cfg)
    add = zscore(proc[("REV20", "raw")] + proc[("LOWTURN20", "raw")])
    assert np.allclose(proc[("ADD", "raw")].to_numpy(), add.to_numpy(), equal_nan=True)


def test_turnover_window_skips_suspended_days(panel):
    x = inputs(panel)
    t, code = panel.dates[200], panel.codes[3]
    window = panel["turn"][code].iloc[181:201].where(panel["tradestatus"][code].iloc[181:201] == 1)
    assert np.isclose(x["turn20"].loc[t, code], window.mean())
