import numpy as np
import pandas as pd

from revturn.panel import (
    adjusted_index,
    asof_industry,
    asof_membership,
    expiry_session,
    front_month_returns,
    front_month_symbol,
    limit_flags,
    limit_pct,
    listed_days,
)


def test_adjusted_index_keeps_dividend_holders_whole():
    dates = pd.bdate_range("2024-01-01", periods=3)
    close = pd.DataFrame({"a": [10.0, 9.5, 9.975]}, index=dates)
    # Day 2 goes ex-dividend 0.5: the exchange reference price is 9.5, so close 9.5 is flat.
    preclose = pd.DataFrame({"a": [10.0, 9.5, 9.5]}, index=dates)
    a = adjusted_index(close, preclose)["a"]
    assert np.isclose(a.iloc[1] / a.iloc[0], 1.0)
    assert np.isclose(a.iloc[2] / a.iloc[1], 1.05)


def test_adjusted_index_matches_vendor_back_adjustment_ratio():
    # Back-adjusted price = raw * factor, factor stepping up by preclose ratio on ex-dates.
    dates = pd.bdate_range("2024-01-01", periods=4)
    raw = pd.Series([20.0, 20.4, 10.3, 10.5], index=dates)  # 2-for-1 split on day 3
    pre = pd.Series([20.0, 20.0, 10.2, 10.3], index=dates)
    factor = (raw.shift(1) / pre).fillna(1.0).cumprod()
    vendor = raw * factor
    ours = adjusted_index(raw.to_frame("x"), pre.to_frame("x"))["x"]
    assert np.allclose((ours / ours.iloc[0]).to_numpy(), (vendor / vendor.iloc[0]).to_numpy())


def test_limit_pct_by_board_and_date():
    dates = pd.DatetimeIndex(["2020-08-21", "2020-08-24", "2026-07-06"])
    codes = pd.Index(
        ["sh.600000", "sz.300750", "sh.688981", "sz.000002", "sz.300001", "sh.688002", "sz.302132"]
    )
    st = pd.DataFrame(0, index=dates, columns=codes)
    st.loc[:, ["sz.000002", "sz.300001", "sh.688002"]] = 1
    p = limit_pct(codes, dates, st)
    assert p.loc["2020-08-21"].tolist() == [0.10, 0.10, 0.20, 0.05, 0.05, 0.20, 0.10]
    assert p.loc["2020-08-24"].tolist() == [0.10, 0.20, 0.20, 0.05, 0.20, 0.20, 0.20]
    assert p.loc["2026-07-06"].tolist() == [0.10, 0.20, 0.20, 0.10, 0.20, 0.20, 0.20]


def test_limit_flags_round_half_up():
    idx = pd.DatetimeIndex(["2024-01-02"])
    pre = pd.DataFrame({"a": [10.05], "b": [10.05], "c": [10.05]}, index=idx)
    pct = pd.DataFrame(0.10, index=idx, columns=pre.columns)
    # 10.05 * 1.1 = 11.055 -> 11.06 ; 10.05 * 0.9 = 9.045 -> 9.05
    op = pd.DataFrame({"a": [11.06], "b": [11.05], "c": [9.05]}, index=idx)
    up, down = limit_flags(op, pre, pct)
    assert up.iloc[0].tolist() == [True, False, False]
    assert down.iloc[0].tolist() == [False, False, True]


def test_membership_is_as_of_and_never_early():
    snaps = pd.DataFrame(
        {
            "updateDate": ["2024-06-10", "2024-06-10", "2024-06-17", "2024-06-17"],
            "code": ["a", "b", "a", "c"],
        }
    )
    dates = pd.DatetimeIndex(["2024-06-07", "2024-06-14", "2024-06-17", "2024-06-18"])
    m = asof_membership(snaps, dates, pd.Index(["a", "b", "c"]))
    assert m.loc["2024-06-07"].tolist() == [False, False, False]
    assert m.loc["2024-06-14"].tolist() == [True, True, False]  # new list not yet effective
    assert m.loc["2024-06-17"].tolist() == [True, False, True]
    assert m.loc["2024-06-18"].tolist() == [True, False, True]


def test_membership_stale_vendor_snapshot_stays_stale():
    snaps = pd.DataFrame(
        {
            "updateDate": ["2022-08-01"] * 2,
            "code": ["a", "b"],
            "query_date": ["2022-12-12", "2023-01-09"],
        }
    )
    m = asof_membership(snaps, pd.DatetimeIndex(["2023-01-10"]), pd.Index(["a", "b"]))
    assert m.iloc[0].tolist() == [True, True]


def test_industry_as_of_start_date():
    hist = pd.DataFrame(
        {
            "symbol": ["600000", "600000"],
            "start_date": ["2014-02-21", "2021-07-30"],
            "industry_code": ["480101", "480301"],
            "update_time": ["2020-01-01", "2025-12-15"],
        }
    )
    dates = pd.DatetimeIndex(["2013-01-04", "2021-07-29", "2021-07-30"])
    ind = asof_industry(hist, dates, pd.Index(["sh.600000", "sz.000001"]))
    col = ind["sh.600000"]
    assert pd.isna(col.iloc[0]) and col.iloc[1:].tolist() == ["48", "48"]
    assert ind["sz.000001"].isna().all()


def test_listed_days_counts_sessions():
    dates = pd.bdate_range("2024-01-01", periods=10)
    ld = listed_days(pd.Series({"a": "2024-01-03", "b": "2023-12-01"}), dates)
    assert ld["a"].tolist()[:3] == [-2, -1, 0]
    assert ld["b"].iloc[0] == 0


def test_front_month_symbol_rolls_after_expiry():
    dates = pd.bdate_range("2025-01-01", "2025-03-31")
    assert expiry_session(2025, 1, dates) == pd.Timestamp("2025-01-17")
    assert front_month_symbol(pd.Timestamp("2025-01-17"), dates) == "IC2501"
    assert front_month_symbol(pd.Timestamp("2025-01-20"), dates) == "IC2502"


def test_front_month_returns_use_one_contract_per_day():
    dates = pd.bdate_range("2025-01-15", "2025-01-22")
    c1 = pd.DataFrame({"date": dates[:4], "close": [100.0, 101.0, 102.0, 103.0]})
    c2 = pd.DataFrame({"date": dates, "close": [90.0, 91.0, 92.0, 93.0, 99.0, 100.0]})
    r = front_month_returns({"IC2501": c1, "IC2502": c2}, dates)
    assert np.isnan(r.iloc[0])
    assert np.isclose(r.loc["2025-01-17"], 102 / 101 - 1)  # expiry day, old contract
    assert np.isclose(r.loc["2025-01-20"], 93 / 92 - 1)  # first day on the new contract
