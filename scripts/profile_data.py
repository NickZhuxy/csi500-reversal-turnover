"""Data profile and processing-cost benchmarks for docs/DATA_PROFILE.md.

Measures data scale, field distributions, vendor quirks, and the effect of each cost reduction.
It never relates a signal to a forward return: the IC benchmark uses a shuffled label.

    uv run python scripts/profile_data.py [--config configs/default.json]
"""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

from revturn.config import PROJECT_ROOT, load_config
from revturn.factors import processed_signals
from revturn.panel import adjusted_index, load_kdata, load_panel
from revturn.stats import rank_ic
from revturn.universe import eligible, entry_tradable


def timed(fn, *a, repeat: int = 1, **k):
    best, out = float("inf"), None
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = fn(*a, **k)
        best = min(best, time.perf_counter() - t0)
    return out, best


def mb(n_bytes: float) -> float:
    return round(n_bytes / 1e6, 1)


def scale(cfg, raw: Path, scratch: Path) -> dict:
    kdir = raw / "baostock" / "kdata"
    files = list(kdir.glob("*.parquet"))
    k, t_read = timed(load_kdata, kdir)
    out = {
        "raw_files_total": len(list(raw.rglob("*.parquet"))),
        "raw_bytes_total_mb": mb(sum(p.stat().st_size for p in raw.rglob("*.parquet"))),
        "kdata_files": len(files),
        "kdata_rows": int(len(k)),
        "kdata_fields": int(k.shape[1]),
        "kdata_parquet_mb": mb(sum(p.stat().st_size for p in files)),
        "kdata_memory_mb_typed": mb(k.memory_usage(deep=True).sum()),
        "kdata_load_and_type_s": round(t_read, 2),
        "date_range": [str(k.date.min().date()), str(k.date.max().date())],
    }
    # Storage format: one CSV versus Parquet for the same typed table.
    csv = scratch / "kdata.csv"
    _, t_csv_w = timed(k.to_csv, csv, index=False)
    _, t_csv_r = timed(pd.read_csv, csv, repeat=2)
    pq = scratch / "kdata.parquet"
    _, t_pq_w = timed(k.to_parquet, pq, index=False)
    _, t_pq_r = timed(pd.read_parquet, pq, repeat=2)
    out["format_benchmark"] = {
        "csv_mb": mb(csv.stat().st_size),
        "csv_read_s": round(t_csv_r, 2),
        "csv_write_s": round(t_csv_w, 2),
        "parquet_mb": mb(pq.stat().st_size),
        "parquet_read_s": round(t_pq_r, 2),
        "parquet_write_s": round(t_pq_w, 2),
    }
    csv.unlink()
    pq.unlink()
    # dtype choices: object codes versus category; float64 versus float32 prices.
    k2 = k.copy()
    before = k2.memory_usage(deep=True).sum()
    k2["code"] = k2.code.astype("category")
    after_cat = k2.memory_usage(deep=True).sum()
    num = k2.select_dtypes("float64").columns
    k2[num] = k2[num].astype("float32")
    after_f32 = k2.memory_usage(deep=True).sum()
    rel = (k2["close"].astype("float64") - k["close"]).abs() / k["close"]
    out["dtype_benchmark"] = {
        "typed_mb": mb(before),
        "code_as_category_mb": mb(after_cat),
        "plus_float32_mb": mb(after_f32),
        "float32_close_max_rel_error": float(rel.max()),
        "note": "float32 kept out: amount needs > 7 significant digits",
    }
    # Windowed download: rows fetched versus fetching every code's full 2016-07..2026-10 span.
    cal = pd.read_parquet(raw / "baostock" / "trade_dates.parquet")
    days = pd.to_datetime(cal.loc[cal.is_trading_day == "1", "calendar_date"])
    full_days = int(((days >= "2016-07-01") & (days <= "2026-10-26")).sum())
    basic = pd.read_parquet(raw / "baostock" / "stock_basic.parquet").set_index("code")
    ipo = pd.to_datetime(basic.ipoDate.reindex(k.code.unique()))
    listed = np.array(
        [
            int(((days >= max(d, pd.Timestamp("2016-07-01"))) & (days <= "2026-10-26")).sum())
            for d in ipo
        ]
    )
    out["windowed_download"] = {
        "rows_fetched": int(len(k)),
        "rows_if_full_span": int(listed.sum()),
        "calendar_days_full_span": full_days,
        "seconds_per_1000_rows_observed": None,
    }
    log = raw / "baostock" / "download_log.jsonl"
    if log.exists():
        ev = [json.loads(x) for x in log.read_text().splitlines()]
        ts = [pd.Timestamp(e["ts"]) for e in ev if e["event"] in ("membership_done", "done")]
        prog = [e for e in ev if e["event"] == "kdata_progress"]
        if len(prog) > 1:
            t0, t1 = pd.Timestamp(prog[0]["ts"]), pd.Timestamp(prog[-1]["ts"])
            rows = len(k) * (prog[-1]["i"] - prog[0]["i"]) / max(prog[-1]["of"], 1)
            out["windowed_download"]["seconds_per_1000_rows_observed"] = round(
                (t1 - t0).total_seconds() / rows * 1000, 2
            )
        out["windowed_download"]["log_events"] = len(ev)
        out["windowed_download"]["timestamps"] = [str(t) for t in ts]
    return out, k


def ex_rights_rows(k: pd.DataFrame) -> int:
    """Rows whose exchange reference price differs from the previous close (dividends, splits)."""
    same = (k.code == k.code.shift(1)).fillna(False).to_numpy(bool)
    prev = k.close.shift(1).to_numpy(float)
    diff = np.abs(k.preclose.to_numpy(float) - prev) > 1e-6
    return int((same & diff).sum())


def distributions(k: pd.DataFrame, panel, cfg) -> dict:
    tr = k[k.tradestatus == 1]
    susp = k[k.tradestatus == 0]
    turn = tr.turn
    ret = k.close / k.preclose - 1
    f = panel.fields
    member = f["member"]
    in_dev = (panel.dates >= "2018-01-02") & (panel.dates <= "2024-12-31")
    m = member.loc[in_dev]
    lim_up = f["open_limit_up"].loc[in_dev] & m
    lim_dn = f["open_limit_down"].loc[in_dev] & m
    one_price = ((f["high"] == f["low"]) & (f["tradestatus"] == 1)).loc[in_dev] & m
    member_days = int(m.to_numpy().sum())
    out = {
        "suspended_rows": int(len(susp)),
        "suspended_share": float(len(susp) / len(k)),
        "suspended_turn_empty_share": float(susp.turn.isna().mean()) if len(susp) else None,
        "suspended_ohlc_equal_preclose_share": float(
            ((susp.open == susp.preclose) & (susp.close == susp.preclose)).mean()
        )
        if len(susp)
        else None,
        "traded_turn_quantiles_pct": {
            str(q): float(turn.quantile(q))
            for q in (0, 0.001, 0.01, 0.25, 0.5, 0.75, 0.99, 0.999, 1)
        },
        "traded_turn_zero_rows": int((turn == 0).sum()),
        "traded_turn_below_0.05pct_rows": int((turn < 0.05).sum()),
        "traded_volume_zero_rows": int((tr.volume == 0).sum()),
        "daily_return_quantiles": {
            str(q): float(ret.quantile(q)) for q in (0.0001, 0.001, 0.01, 0.5, 0.99, 0.999, 0.9999)
        },
        "abs_daily_return_over_21pct_rows": int((ret.abs() > 0.21).sum()),
        "ex_rights_rows": ex_rights_rows(k),
        "dev_member_days": member_days,
        "dev_open_limit_up_member_days": int(lim_up.to_numpy().sum()),
        "dev_open_limit_down_member_days": int(lim_dn.to_numpy().sum()),
        "dev_one_price_bar_member_days": int(one_price.to_numpy().sum()),
        "dev_st_member_days": int(((f["is_st"] == 1).loc[in_dev] & m).to_numpy().sum()),
    }
    top = turn.nlargest(5)
    out["largest_traded_turn_rows"] = (
        k.loc[top.index, ["date", "code", "turn", "pctChg"]].astype(str).to_dict(orient="records")
    )
    big = ret.abs().nlargest(5)
    out["largest_abs_return_rows"] = (
        k.loc[big.index, ["date", "code", "preclose", "close", "tradestatus"]]
        .astype(str)
        .to_dict(orient="records")
    )
    elig = eligible(panel, cfg)
    ent = entry_tradable(panel)
    e = elig.loc[in_dev]
    out["dev_mean_members"] = float(m.sum(axis=1).mean())
    out["dev_mean_eligible"] = float(e.sum(axis=1).mean())
    out["dev_mean_eligible_and_tradable"] = float((e & ent.loc[in_dev]).sum(axis=1).mean())
    drop = {
        "st": (f["is_st"] == 1),
        "suspended": f["tradestatus"] != 1,
        "young": f["listed_days"] < cfg.min_listed_days,
    }
    out["dev_member_exclusion_counts"] = {
        k2: int((v.loc[in_dev] & m).to_numpy().sum()) for k2, v in drop.items()
    }
    cap = f["float_cap"].where(member).loc[in_dev]
    out["float_cap_cny_quantiles"] = {
        str(q): float(np.nanquantile(cap.to_numpy(), q)) for q in (0.01, 0.5, 0.99)
    }
    jump = (np.log(f["float_cap"]).diff().abs() > np.log(1.5)).loc[in_dev] & m
    out["float_cap_day_jumps_over_50pct"] = int(jump.to_numpy().sum())
    ind = f["industry"].where(member).loc[in_dev]
    out["dev_member_days_without_industry"] = int((ind.isna() & m).to_numpy().sum())
    # Membership churn at each snapshot change.
    chg = member.astype(int).diff().abs().sum(axis=1)
    churn = chg[chg > 0]
    out["membership_change_days"] = int(len(churn))
    out["membership_largest_changes"] = {
        str(d.date()): int(v) for d, v in churn.nlargest(6).items()
    }
    out["delisted_codes_in_universe"] = int(len(panel.meta.get("out_dates", {})))
    fut = panel.benchmarks["fut_ret"]
    first = fut.first_valid_index()
    out["futures_hedge"] = {
        "first_return_date": str(first.date()) if first is not None else None,
        "missing_days_after_first": int(fut.loc[first:].isna().sum())
        if first is not None
        else None,
    }
    b = panel.benchmarks
    basis = None
    ic0 = cfg.raw_dir / "akshare" / "futures" / "IC0_main_sina.parquet"
    if ic0.exists():
        m = pd.read_parquet(ic0)
        m = m.assign(date=pd.to_datetime(m["日期"]), f=pd.to_numeric(m["收盘价"])).set_index("date")
        spot = b["px_close"].reindex(m.index)
        basis = ((m.f - spot) / spot).dropna()
        out["ic_main_basis_pct_by_year"] = {
            str(y): round(float(v) * 100, 2)
            for y, v in basis.groupby(basis.index.year).mean().items()
        }
    return out


def adjustment_check(raw: Path) -> dict:
    """Compare the local close/preclose index with BaoStock's own back-adjusted series."""
    rows = []
    for p in sorted((raw / "baostock" / "kdata_adj_check").glob("*.parquet")):
        v = pd.read_parquet(p)
        k = pd.read_parquet(raw / "baostock" / "kdata" / p.name)
        v = v.assign(date=pd.to_datetime(v.date), vc=pd.to_numeric(v.close)).set_index("date")
        k = k.assign(
            date=pd.to_datetime(k.date), c=pd.to_numeric(k.close), pc=pd.to_numeric(k.preclose)
        ).set_index("date")
        ours = adjusted_index(k[["c"]], k[["pc"]].rename(columns={"pc": "c"}))["c"]
        common = ours.index.intersection(v.index)
        if len(common) < 50:
            continue
        a = ours.loc[common] / ours.loc[common].iloc[0]
        b = v.vc.loc[common] / v.vc.loc[common].iloc[0]
        r_a, r_b = a.pct_change().dropna(), b.pct_change().dropna()
        rows.append(
            {
                "code": p.stem,
                "days": len(common),
                "max_abs_daily_return_diff": float((r_a - r_b).abs().max()),
                "end_ratio_diff": float(a.iloc[-1] / b.iloc[-1] - 1),
            }
        )
    df = pd.DataFrame(rows)
    return {
        "codes": len(df),
        "rows": df.to_dict(orient="records"),
        "worst_daily_diff": float(df.max_abs_daily_return_diff.max()) if len(df) else None,
        "worst_end_ratio_diff": float(df.end_ratio_diff.abs().max()) if len(df) else None,
    }


def compute_benchmarks(panel, cfg) -> dict:
    """Wall-clock and memory of the main computations, plus the slow alternatives replaced."""
    out = {}
    elig = eligible(panel, cfg)
    sig, t_sig = timed(processed_signals, panel, elig, cfg)
    out["processed_signals_10_series_s"] = round(t_sig, 1)
    tracemalloc.start()  # second pass for memory only: tracing slows Python several-fold
    processed_signals(panel, elig, cfg)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    out["processed_signals_peak_traced_mb"] = mb(peak)
    s = sig[("RxT", "neutral")]
    rng = np.random.default_rng(0)
    placebo = pd.DataFrame(
        rng.permutation(s.to_numpy().ravel()).reshape(s.shape), index=s.index, columns=s.columns
    )  # shuffled: no information
    mask = elig & entry_tradable(panel)
    _, t_vec = timed(rank_ic, s, placebo, mask, cfg.min_names, repeat=3)
    rows = s.index[mask.sum(axis=1) >= cfg.min_names][:250]

    def loop(rows):
        out = []
        for d in rows:
            ok = mask.loc[d] & s.loc[d].notna() & placebo.loc[d].notna()
            out.append(sps.spearmanr(s.loc[d][ok], placebo.loc[d][ok]).statistic)
        return out

    _, t_loop = timed(loop, rows)
    n_rows = int((mask.sum(axis=1) >= cfg.min_names).sum())
    out["rank_ic_vectorized_all_days_s"] = round(t_vec, 3)
    out["rank_ic_scipy_loop_250_days_s"] = round(t_loop, 3)
    out["rank_ic_scipy_loop_extrapolated_all_days_s"] = round(t_loop / len(rows) * n_rows, 2)
    out["rank_ic_days"] = n_rows
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(PROJECT_ROOT / "configs" / "default.json"))
    a = ap.parse_args()
    cfg = load_config(a.config)
    scratch = cfg.data_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    result = {"created_at": pd.Timestamp.now().isoformat(timespec="seconds")}
    result["scale"], k = scale(cfg, cfg.raw_dir, scratch)
    panel, t_load = timed(load_panel, cfg.processed_dir)
    result["panel"] = {
        "load_s": round(t_load, 2),
        "panel_parquet_mb": mb((cfg.processed_dir / "panel.parquet").stat().st_size),
        "wide_shape": list(panel["close"].shape),
        "wide_fields": len(panel.fields),
        "wide_memory_mb": mb(sum(v.memory_usage(deep=True).sum() for v in panel.fields.values())),
        "wide_cells_with_bar_share": float(panel["has_row"].to_numpy().mean()),
    }
    result["distributions"] = distributions(k, panel, cfg)
    result["adjustment_check"] = adjustment_check(cfg.raw_dir)
    result["compute"] = compute_benchmarks(panel, cfg)
    out = PROJECT_ROOT / "docs" / "data_profile.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    print(out)


if __name__ == "__main__":
    main()
