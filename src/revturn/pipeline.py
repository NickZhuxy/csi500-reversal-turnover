"""One run = one split: IC family, quintiles, long-only simulation, pass/fail, artifacts."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

from . import __version__
from .config import Config, load_config
from .factors import SIGNALS, VERSIONS, processed_signals
from .freeze import check_freeze, sha256_file, source_hash
from .labels import after_last_bar, exit_prices, forward_returns, label_end_ok
from .panel import Panel, load_panel
from .portfolio import (
    monthly_hit_rate,
    perf,
    quantile_buckets,
    quintile_study,
    schedule,
    simulate_long,
)
from .stats import bh_qvalues, hac_lags, hac_test, ic_summary, per_year, rank_ic
from .universe import buyable_at_open, eligible, entry_tradable, sellable_at_open


def _split_rows(dates: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    return np.asarray((dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end)))


def ic_family(cfg, sig, labels, mask, dates, split) -> tuple[pd.DataFrame, dict]:
    sp = cfg.splits[split]
    base = _split_rows(dates, sp.start, sp.end)
    rows, series = [], {}
    for name in SIGNALS:
        for ver in VERSIONS:
            for h in cfg.horizons:
                keep = base & label_end_ok(dates, h, sp.label_end).to_numpy()
                d = dates[keep]
                ic = rank_ic(sig[(name, ver)].loc[d], labels[h].loc[d], mask.loc[d], cfg.min_names)
                series[(name, ver, h)] = ic
                rows.append({"signal": name, "version": ver, "horizon": h, **ic_summary(ic, h)})
    tab = pd.DataFrame(rows)
    tab["bh_q"] = bh_qvalues(tab.p_value.to_numpy())
    return tab, series


def monotonicity(means: np.ndarray) -> float:
    """Spearman correlation of bucket number with mean return, rounded to absorb float error.

    scipy returns 0.8999999999999998 for one adjacent swap of five buckets; rounding keeps the
    pre-registered `>= 0.9` threshold meaning "at most one adjacent swap".
    """
    ranks = sps.rankdata(means)
    k = np.arange(1, len(means) + 1)
    return round(float(np.corrcoef(k, ranks)[0, 1]), 10)


def quintile_summary(q: pd.DataFrame, n_q: int) -> dict:
    wide = q.pivot(index="signal_date", columns="bucket", values="ret")
    turn = q.pivot(index="signal_date", columns="bucket", values="turnover")
    means = wide.mean()
    return {
        "bucket_mean_monthly": {int(k): float(v) for k, v in means.items()},
        "monotonicity": monotonicity(means.to_numpy()),
        "ls_mean_monthly_gross": float((wide[n_q] - wide[1]).mean()),
        "top_turnover_two_way": float(turn[n_q].iloc[1:].mean()),
        "n_months": int(len(wide)),
        "names_without_exit_price": int(q.n_formed.sum() - q.n.sum()),
    }


def bench_returns(panel: Panel) -> tuple[dict[str, pd.Series], int]:
    """Daily benchmark returns. A missing index close is carried forward (and counted), so one
    vendor gap cannot silently delete two strategy days from every hedged series."""
    b = panel.benchmarks
    gaps = int(b["tr_close"].isna().sum() + b["px_close"].isna().sum())
    return {
        "tr": b["tr_close"].ffill().pct_change(),
        "px": b["px_close"].ffill().pct_change(),
        "fut": b["fut_ret"],
    }, gaps


def bucket_targets(signal, mask, sch, n_q: int, bucket: int) -> dict[int, list[str]]:
    out = {}
    for t, i in zip(sch.signal_dates, sch.exec_idx):
        b = quantile_buckets(signal.loc[t].where(mask.loc[t]), n_q)
        out[i] = list(b.index[b == bucket])
    return out


def long_short_daily(panel: Panel, cfg: Config, signal, mask, split: str) -> dict:
    """Protocol section 8: Q5 - Q1 as the daily return difference of two drifting books.

    Names are formed among eligible names tradable at the next open (the symmetric research
    filter), so both books trade without further blocking. Costs hurt both legs: the short leg's
    return is -(gross return) - (its cost drag), where the cost drag is measured by running the
    same book with and without costs.
    """
    sp = cfg.splits[split]
    sch = schedule(panel.dates, sp.start, sp.end, sp.label_end)
    free = pd.DataFrame(True, index=panel.dates, columns=panel.codes)
    one = pd.DataFrame(1.0, index=panel.dates, columns=panel.codes)
    top, bottom = cfg.n_quantiles, 1
    books = {q: bucket_targets(signal, mask, sch, cfg.n_quantiles, q) for q in (bottom, top)}

    def book(q: int, bps: float) -> pd.Series:
        return simulate_long(
            books[q],
            panel["adj_open"],
            panel["adj_close"],
            free,
            free,
            one,
            sch.exec_idx[0],
            sch.end_idx,
            bps / 1e4,
        ).daily

    short_gross = book(bottom, 0.0)
    out = {}
    for label, c in (("gross", 0.0), ("net", cfg.cost_bps), ("stress", cfg.stress_cost_bps)):
        short_drag = short_gross - book(bottom, c) if c else 0.0
        ls = book(top, c) - short_gross - short_drag
        out[f"ls_{label}"] = perf(ls, cfg.trading_days_per_year)
    return out


def long_only(
    panel: Panel, cfg: Config, signal: pd.DataFrame, elig: pd.DataFrame, split: str
) -> dict:
    sp = cfg.splits[split]
    sch = schedule(panel.dates, sp.start, sp.end, sp.label_end)
    targets = {}
    for t, i in zip(sch.signal_dates, sch.exec_idx):
        s = signal.loc[t].where(elig.loc[t]).dropna()
        top = s.rank(pct=True) > 1 - 1 / cfg.n_quantiles
        targets[i] = list(s.index[top])
    adv = panel["amount"].rolling(20, min_periods=10).mean().shift(1)
    args = (
        targets,
        panel["adj_open"],
        panel["adj_close"],
        buyable_at_open(panel),
        sellable_at_open(panel),
        adv,
        sch.exec_idx[0],
        sch.end_idx,
    )
    bench, _ = bench_returns(panel)
    out = {}
    for label, c in (("base", cfg.cost_bps), ("stress", cfg.stress_cost_bps)):
        res = simulate_long(*args, cost=c / 1e4, participation=cfg.capacity_participation)
        r = res.daily
        rec = {
            "cost_bps": c,
            "absolute": perf(r, cfg.trading_days_per_year),
            "turnover_two_way_monthly": float(res.turnover.iloc[1:].mean()),
            "avg_names_held": float(res.n_held.mean()),
            "blocked_buys": res.blocked_buys,
            "blocked_sells": res.blocked_sells,
            "forced_exits": res.forced_exits,
        }
        for h, br in bench.items():
            ex = (r - br.reindex(r.index)).dropna()
            rec[f"hedged_{h}"] = perf(ex, cfg.trading_days_per_year)
            rec[f"hedged_{h}"]["monthly_hit_rate"] = monthly_hit_rate(ex)
            rec[f"hedged_{h}"]["first_date"] = str(ex.index.min().date()) if len(ex) else None
        cap = np.asarray(res.capacity_per_trade)
        if len(cap):
            rec["capacity_aum_cny"] = {
                "median_trade": float(np.median(cap)),
                "p10_trade": float(np.percentile(cap, 10)),
            }
        out[label] = rec
        out[f"_daily_{label}"] = r
    return out


def post_hoc_diagnostics(panel, cfg, primary, elig, bench, split) -> dict:
    """Added after the first dev run (EXPERIMENT_LOG E4). Descriptive only; no new signal and no
    effect on the verdict. They separate universe drift, costs, and futures basis from the signal."""
    sp = cfg.splits[split]
    sch = schedule(panel.dates, sp.start, sp.end, sp.label_end)
    args = (
        panel["adj_open"],
        panel["adj_close"],
        buyable_at_open(panel),
        sellable_at_open(panel),
        pd.DataFrame(1.0, index=panel.dates, columns=panel.codes),
        sch.exec_idx[0],
        sch.end_idx,
    )
    tr = bench["tr"]
    out = {}
    ew = {
        i: list(elig.columns[elig.loc[t].to_numpy()])
        for t, i in zip(sch.signal_dates, sch.exec_idx)
    }
    for label, c in (("gross", 0.0), ("base", cfg.cost_bps)):
        r = simulate_long(ew, *args, cost=c / 1e4).daily
        out[f"ew_eligible_universe_vs_tr_{label}"] = perf((r - tr.reindex(r.index)).dropna())
    top = {}
    for t, i in zip(sch.signal_dates, sch.exec_idx):
        x = primary.loc[t].where(elig.loc[t]).dropna()
        top[i] = list(x.index[x.rank(pct=True) > 1 - 1 / cfg.n_quantiles])
    r = simulate_long(top, *args, cost=0.0).daily
    out["primary_q5_vs_tr_gross"] = perf((r - tr.reindex(r.index)).dropna())
    days = r.index[bench["fut"].reindex(r.index).notna().to_numpy()]
    if len(days):
        drag = (bench["px"] - bench["fut"]).reindex(days)
        out["futures_basis_drag"] = {
            "window": [str(days.min().date()), str(days.max().date())],
            "annualized_px_minus_fut": float(drag.mean() * cfg.trading_days_per_year),
        }
    return out


def evaluate_pass(
    cfg: Config, split: str, primary_row: dict, years: pd.DataFrame, quint: dict, lo: dict
) -> dict:
    th = cfg.pass_thresholds
    tr_base = lo["base"]["hedged_tr"]
    tr_stress = lo["stress"]["hedged_tr"]
    if split == "dev":
        checks = {
            "P1_mean_ic": primary_row["mean_ic"] >= th["min_mean_ic"],
            "P2_hac_t_and_bh": primary_row["hac_t"] >= th["min_hac_t"]
            and primary_row["bh_q"] <= th["max_bh_q"],
            "P3_positive_years": int((years.mean_ic > 0).sum()) >= th["min_positive_years"],
            "P4_monotonic": quint["monotonicity"] >= th["min_monotonicity"]
            and quint["ls_mean_monthly_gross"] > 0,
            "P5_hedged_net": tr_base["ann_return"] > 0
            and tr_base["sharpe"] >= th["min_ir"]
            and tr_stress["ann_return"] > 0,
        }
    else:
        checks = {
            "OOS_mean_ic_positive": primary_row["mean_ic"] > 0,
            "OOS_hedged_net_positive": tr_base["ann_return"] > 0,
        }
    return {"checks": {k: bool(v) for k, v in checks.items()}, "pass": bool(all(checks.values()))}


def run(cfg_path: str | Path, split: str, panel: Panel | None = None, note: str = "") -> Path:
    cfg = load_config(cfg_path)
    freeze = check_freeze(Path(cfg_path)) if split in cfg.oos_splits else None
    if split not in cfg.splits:
        raise ValueError(f"unknown split {split}")
    sp = cfg.splits[split]
    panel = panel or load_panel(cfg.processed_dir)
    dates = panel.dates

    elig = eligible(panel, cfg)
    entry_ok = entry_tradable(panel)
    mask = elig & entry_ok
    sig = processed_signals(panel, elig, cfg)
    labels = {h: forward_returns(panel, h) for h in cfg.horizons}
    bench, bench_gaps = bench_returns(panel)

    tab, series = ic_family(cfg, sig, labels, mask, dates, split)
    pkey = (cfg.primary_signal, cfg.primary_version, cfg.primary_horizon)
    prow = tab[(tab.signal == pkey[0]) & (tab.version == pkey[1]) & (tab.horizon == pkey[2])]
    primary_row = prow.iloc[0].to_dict()
    years = per_year(series[pkey], pkey[2])
    years_all = pd.concat(
        [
            per_year(series[(n, v, 20)], 20).assign(signal=n, version=v)
            for n in SIGNALS
            for v in VERSIONS
        ]
    )

    d_ic = series[("RxT", "neutral", 20)] - series[("REV20", "neutral", 20)]
    h3_t, h3_p = hac_test(d_ic, hac_lags(20))
    h3 = {"mean_ic_diff": float(d_ic.mean()), "hac_t": h3_t, "p_value": h3_p}

    rows = dates[_split_rows(dates, sp.start, sp.end)]
    corr = {}
    for ver in VERSIONS:
        m = pd.DataFrame(np.eye(len(SIGNALS)), index=SIGNALS, columns=SIGNALS)
        for a in range(len(SIGNALS)):
            for b in range(a + 1, len(SIGNALS)):
                x, y = sig[(SIGNALS[a], ver)].loc[rows], sig[(SIGNALS[b], ver)].loc[rows]
                v = float(rank_ic(x, y, elig.loc[rows], 30).mean())
                m.iloc[a, b] = m.iloc[b, a] = v
        corr[ver] = m

    sch = schedule(dates, sp.start, sp.end, sp.label_end)
    ex_px = exit_prices(panel)
    quint, quint_rows = {}, []
    for name in SIGNALS:
        for ver in VERSIONS:
            q = quintile_study(
                sig[(name, ver)],
                mask,
                panel["adj_open"],
                ex_px,
                panel["adj_close"],
                sch,
                cfg.n_quantiles,
            )
            quint[(name, ver)] = {
                **quintile_summary(q, cfg.n_quantiles),
                **long_short_daily(panel, cfg, sig[(name, ver)], mask, split),
            }
            quint_rows.append(q.assign(signal=name, version=ver))

    lo = {
        (n, v): long_only(panel, cfg, sig[(n, v)], elig, split) for n in SIGNALS for v in VERSIONS
    }
    plo = lo[(pkey[0], pkey[1])]
    post = post_hoc_diagnostics(panel, cfg, sig[(pkey[0], pkey[1])], elig, bench, split)
    verdict = evaluate_pass(cfg, split, primary_row, years, quint[(pkey[0], pkey[1])], plo)
    secondary = None
    if cfg.confirmatory_secondary:
        c2 = cfg.confirmatory_secondary
        k2 = (c2["signal"], c2["version"], c2["horizon"])
        row2 = tab[(tab.signal == k2[0]) & (tab.version == k2[1]) & (tab.horizon == k2[2])]
        row2 = row2.iloc[0].to_dict()
        lo2 = lo[(k2[0], k2[1])]["base"]["hedged_tr"]
        checks2 = {
            "OOS_mean_ic_positive": row2["mean_ic"] > 0,
            "OOS_hedged_net_positive": lo2["ann_return"] > 0,
        }
        secondary = {
            "key": list(k2),
            "registered": "2026-10-27, after dev results, before OOS (EXPERIMENT_LOG E6)",
            "applies_to": "out-of-sample splits only; in dev it is descriptive",
            "mean_ic": row2["mean_ic"],
            "hac_t": row2["hac_t"],
            "hedged_tr_base": lo2,
            "checks": {k: bool(v) for k, v in checks2.items()},
            "pass": bool(all(checks2.values())) if split in cfg.oos_splits else None,
        }

    # Pre-registered robustness (descriptive only).
    psig = sig[(pkey[0], pkey[1])]
    keep = _split_rows(dates, sp.start, sp.end) & label_end_ok(dates, 20, sp.label_end).to_numpy()
    d = dates[keep]
    robust = {}
    ic_nofilter = rank_ic(psig.loc[d], labels[20].loc[d], elig.loc[d], cfg.min_names)
    robust["R2_no_tradability_filter_ic"] = ic_summary(ic_nofilter, 20)
    q_nf = quintile_study(
        psig, elig, panel["adj_open"], ex_px, panel["adj_close"], sch, cfg.n_quantiles
    )
    robust["R2_no_tradability_filter_quintiles"] = {
        **quintile_summary(q_nf, cfg.n_quantiles),
        **long_short_daily(panel, cfg, psig, elig, split),
    }
    s0, s1 = (pd.Timestamp(x) for x in cfg.stale_membership_window)
    stale = (series[pkey].index >= s0) & (series[pkey].index <= s1)
    robust["R3_drop_stale_membership_ic"] = ic_summary(series[pkey][~stale], 20)
    robust["R3_dropped_days"] = int(stale.sum())
    if cfg.all_stale_membership_windows:
        idx = series[pkey].index
        drop = np.zeros(len(idx), dtype=bool)
        for w0, w1 in cfg.all_stale_membership_windows:
            drop |= (idx >= pd.Timestamp(w0)) & (idx <= pd.Timestamp(w1))
        robust["R3b_drop_all_stale_membership_ic"] = ic_summary(series[pkey][~drop], 20)
        robust["R3b_dropped_days"] = int(drop.sum())
    c0, c1 = (pd.Timestamp(x) for x in cfg.comparable_subperiod)
    sub = (series[pkey].index >= c0) & (series[pkey].index <= c1)
    if sub.any():
        robust["R4_subperiod_ic"] = ic_summary(series[pkey][sub], 20)
        db = plo["_daily_base"]
        ex = (db - bench["tr"].reindex(db.index)).dropna()
        robust["R4_subperiod_hedged_tr_base"] = perf(ex[(ex.index >= c0) & (ex.index <= c1)])

    # Universe and label diagnostics.
    split_rows = _split_rows(dates, sp.start, sp.end)
    h = cfg.primary_horizon
    filled = after_last_bar(panel).shift(-(1 + h), fill_value=False).astype(bool)
    diag = {
        "labels_using_last_close_after_final_bar_h20": int((filled & mask).loc[d].to_numpy().sum()),
        "benchmark_missing_closes_filled": bench_gaps,
        "mean_members": float(panel["member"].loc[split_rows].sum(axis=1).mean()),
        "mean_eligible": float(elig.loc[split_rows].sum(axis=1).mean()),
        "mean_eligible_and_tradable": float(mask.loc[split_rows].sum(axis=1).mean()),
        "rebalances": len(sch.signal_dates),
    }

    created = dt.datetime.now()
    run_id = f"{split}-{created:%Y%m%d-%H%M%S}"
    out = cfg.runs_dir / run_id
    out.mkdir(parents=True, exist_ok=False)
    (out / "figures").mkdir()
    panel_file = cfg.processed_dir / "panel.parquet"
    summary = {
        "run_id": run_id,
        "split": split,
        "split_dates": {"start": sp.start, "end": sp.end, "label_end": sp.label_end},
        "created_at": created.isoformat(timespec="seconds"),
        "package_version": __version__,
        "note": note,
        "config": cfg.to_json(),
        "config_sha256": sha256_file(Path(cfg_path)),
        "source_sha256": source_hash(),
        "panel_sha256": sha256_file(panel_file) if panel_file.exists() else None,
        "freeze": freeze,
        "primary": {"key": list(pkey), **primary_row},
        "primary_per_year": years.to_dict(orient="records"),
        "h3_rxt_minus_rev20": h3,
        "verdict": verdict,
        "confirmatory_secondary": secondary,
        "quintiles": {f"{n}|{v}": s for (n, v), s in quint.items()},
        "long_only": {
            f"{n}|{v}": {k: x for k, x in r.items() if not k.startswith("_")}
            for (n, v), r in lo.items()
        },
        "robustness": robust,
        "diagnostics": diag,
        "post_hoc_diagnostics": post,
        "n_tests_in_family": int(len(tab)),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_default))
    tab.to_csv(out / "ic_table.csv", index=False)
    years_all.to_csv(out / "ic_per_year_h20.csv", index=False)
    pd.concat(quint_rows).to_csv(out / "quintile_periods.csv", index=False)
    for ver, m in corr.items():
        m.to_csv(out / f"signal_corr_{ver}.csv")
    daily = pd.DataFrame({"long_base": plo["_daily_base"], "long_stress": plo["_daily_stress"]})
    for k, v in bench.items():
        daily[f"{k}_ret"] = v.reindex(daily.index)
    daily.to_csv(out / "primary_daily.csv")
    pd.DataFrame({str(k): v for k, v in series.items() if k[0] == pkey[0]}).to_csv(
        out / "primary_ic_daily.csv"
    )
    from .report import write_report

    write_report(out, summary, tab, years_all, corr, series, pkey, daily, quint_rows)
    return out


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp, dt.date)):
        return str(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def run_from_cli(cfg_path: str, split: str, note: str = "") -> Path:
    return run(cfg_path, split, note=note)
