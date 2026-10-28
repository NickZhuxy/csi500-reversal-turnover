import numpy as np
import pandas as pd
from scipy import stats as sps

from revturn.stats import bh_qvalues, hac_test, ic_summary, newey_west_se, rank_ic


def test_rank_ic_matches_scipy_with_nans_and_ties():
    rng = np.random.default_rng(0)
    s = pd.DataFrame(rng.integers(0, 6, (4, 40)).astype(float))
    lab = pd.DataFrame(rng.normal(size=(4, 40)))
    s.iloc[0, :5] = np.nan
    lab.iloc[1, 10:12] = np.nan
    mask = pd.DataFrame(True, index=s.index, columns=s.columns)
    mask.iloc[2, :8] = False
    ic = rank_ic(s, lab, mask, min_names=10)
    for i in range(4):
        ok = mask.iloc[i] & s.iloc[i].notna() & lab.iloc[i].notna()
        ref = sps.spearmanr(s.iloc[i][ok], lab.iloc[i][ok]).statistic
        assert np.isclose(ic.iloc[i], ref)


def test_rank_ic_needs_min_names():
    s = pd.DataFrame([np.arange(10.0)])
    ic = rank_ic(s, s, pd.DataFrame(True, index=s.index, columns=s.columns), min_names=11)
    assert np.isnan(ic.iloc[0])


def test_newey_west_zero_lags_is_iid_se():
    x = np.random.default_rng(2).normal(size=500)
    assert np.isclose(newey_west_se(x, 0), x.std(ddof=0) / np.sqrt(len(x)))


def test_newey_west_hand_computed():
    x = np.array([1.0, -2.0, 3.0, 0.5, -1.0, 2.0])
    e = x - x.mean()
    t = len(x)
    g = [e @ e / t, e[1:] @ e[:-1] / t, e[2:] @ e[:-2] / t]
    s = g[0] + 2 * (2 / 3) * g[1] + 2 * (1 / 3) * g[2]
    assert np.isclose(newey_west_se(x, 2), np.sqrt(s / t))


def test_hac_widens_for_autocorrelated_series():
    rng = np.random.default_rng(3)
    e = rng.normal(size=3000)
    x = pd.Series(np.convolve(e, np.ones(20), "same") / 20 + 0.01)
    t_iid, _ = hac_test(x, 0)
    t_hac, _ = hac_test(x, 40)
    assert abs(t_hac) < abs(t_iid) / 2


def test_bh_known_example():
    q = bh_qvalues(np.array([0.01, 0.04, 0.03, 0.2, np.nan]))
    assert np.allclose(q[:4], [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.2])
    assert np.isnan(q[4])


def test_ic_summary_fields():
    ic = pd.Series(np.r_[np.full(30, 0.05), np.full(30, -0.01)])
    s = ic_summary(ic, 1)
    assert s["n_days"] == 60 and s["hac_lags"] == 5
    assert np.isclose(s["mean_ic"], 0.02) and np.isclose(s["positive_ratio"], 0.5)
    assert np.isclose(s["icir"], 0.02 / ic.std(ddof=1))


def test_monotonicity_one_adjacent_swap_is_exactly_point_nine():
    from revturn.pipeline import monotonicity

    # scipy.spearmanr gives 0.8999999999999998 here, which would fail a ">= 0.9" rule.
    assert monotonicity(np.array([0.010, 0.012, 0.011, 0.015, 0.020])) == 0.9
    assert monotonicity(np.array([0.01, 0.02, 0.03, 0.04, 0.05])) == 1.0
