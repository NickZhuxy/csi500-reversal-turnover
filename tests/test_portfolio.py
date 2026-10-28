import numpy as np
import pandas as pd

from revturn.portfolio import perf, quantile_buckets, schedule, simulate_long


def flat(dates, codes, value=10.0):
    return pd.DataFrame(value, index=dates, columns=codes)


def test_schedule_month_ends_and_label_end():
    dates = pd.bdate_range("2024-10-01", "2025-01-10")
    sch = schedule(dates, "2024-10-01", "2024-12-31", "2024-12-31")
    assert [str(t.date()) for t in sch.signal_dates] == ["2024-10-31", "2024-11-29"]
    assert dates[sch.exec_idx[0]] == pd.Timestamp("2024-11-01")
    assert dates[sch.end_idx] == pd.Timestamp("2024-12-31")


def test_quantile_buckets_are_balanced():
    b = quantile_buckets(pd.Series(np.arange(100.0)), 5)
    assert b.value_counts().tolist() == [20] * 5
    assert b.iloc[-1] == 5 and b.iloc[0] == 1


def _sim(targets, o, c, buy, sell, cost=0.0, end=None):
    adv = flat(o.index, o.columns, 1e9)
    return simulate_long(targets, o, c, buy, sell, adv, 0, end or len(o) - 1, cost)


def test_costs_are_charged_on_traded_value():
    dates = pd.bdate_range("2024-01-01", periods=3)
    o = c = flat(dates, ["a", "b"])
    ok = pd.DataFrame(True, index=dates, columns=["a", "b"])
    res = _sim({0: ["a", "b"]}, o, c, ok, ok, cost=0.002)
    assert np.isclose(res.daily.iloc[0], -0.002)
    assert np.allclose(res.daily.iloc[1:], 0)
    assert np.isclose(res.turnover.iloc[0], 1.0)


def test_limit_up_open_blocks_buy_and_reweights():
    dates = pd.bdate_range("2024-01-01", periods=2)
    o = c = flat(dates, ["a", "b"])
    buy = pd.DataFrame(True, index=dates, columns=["a", "b"])
    buy.loc[dates[0], "b"] = False
    res = _sim({0: ["a", "b"]}, o, c, buy, buy)
    assert res.blocked_buys == 1 and res.n_held.iloc[0] == 1


def test_limit_down_open_blocks_sell_and_retries():
    dates = pd.bdate_range("2024-01-01", periods=4)
    o = flat(dates, ["a", "b"])
    c = o.copy()
    ok = pd.DataFrame(True, index=dates, columns=["a", "b"])
    sell = ok.copy()
    sell.loc[dates[1], "a"] = False  # cannot sell a on the rebalance open
    res = _sim({0: ["a"], 1: ["b"]}, o, c, ok, sell)
    assert res.blocked_sells == 1
    assert res.n_held.loc[dates[1]] == 2  # still holding a, plus b
    assert res.n_held.loc[dates[2]] == 1  # sold a at the next open


def test_full_switch_turnover_is_two():
    dates = pd.bdate_range("2024-01-01", periods=3)
    o = c = flat(dates, ["a", "b"])
    ok = pd.DataFrame(True, index=dates, columns=["a", "b"])
    res = _sim({0: ["a"], 1: ["b"]}, o, c, ok, ok)
    assert np.isclose(res.turnover.iloc[1], 2.0)


def test_returns_follow_open_and_close_prices():
    dates = pd.bdate_range("2024-01-01", periods=3)
    o = pd.DataFrame({"a": [10.0, 11.0, 12.0]}, index=dates)
    c = pd.DataFrame({"a": [10.5, 11.5, 12.5]}, index=dates)
    ok = pd.DataFrame(True, index=dates, columns=["a"])
    res = _sim({0: ["a"]}, o, c, ok, ok)
    assert np.allclose(res.daily.to_numpy(), [10.5 / 10 - 1, 11.5 / 10.5 - 1, 12.5 / 11.5 - 1])


def test_perf_drawdown_includes_start():
    r = pd.Series([-0.1, 0.05, 0.05])
    p = perf(r, periods=242)
    assert np.isclose(p["max_drawdown"], -0.1)
    assert np.isclose(p["total_return"], 0.9 * 1.05 * 1.05 - 1)
