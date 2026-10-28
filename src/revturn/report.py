"""Human-readable run report (Markdown tables) and three figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

plt.rcParams.update(
    {
        "font.family": "serif",
        "figure.dpi": 120,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def _md(df: pd.DataFrame, floatfmt: str = "{:.4f}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for r in df.to_dict("records"):  # records keep int columns as ints
        cells = []
        for c in cols:
            v = r[c]
            cells.append(floatfmt.format(v) if isinstance(v, float) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(out: Path, summary, tab, years_all, corr, series, pkey, daily, quint_rows):
    s = summary
    lines = [
        f"# Run {s['run_id']}",
        "",
        f"Split `{s['split']}`: signals {s['split_dates']['start']} to "
        f"{s['split_dates']['end']}, labels end by {s['split_dates']['label_end']}.",
        "",
        f"Verdict for the primary ({' / '.join(map(str, pkey))}): "
        f"**{'PASS' if s['verdict']['pass'] else 'FAIL'}**",
        "",
    ]
    lines += [f"- {k}: {'yes' if v else 'no'}" for k, v in s["verdict"]["checks"].items()]
    sec = s.get("confirmatory_secondary")
    if sec:
        res = {True: "PASS", False: "FAIL", None: "descriptive in dev"}[sec["pass"]]
        lines += [
            "",
            f"Post-dev confirmatory test ({' / '.join(map(str, sec['key']))}, "
            f"registered before OOS): **{res}**",
        ]
        lines += [f"- {k}: {'yes' if v else 'no'}" for k, v in sec["checks"].items()]
    lines += ["", "## Rank IC family", ""]
    t = tab[
        [
            "signal",
            "version",
            "horizon",
            "n_days",
            "mean_ic",
            "icir",
            "positive_ratio",
            "hac_t",
            "p_value",
            "bh_q",
        ]
    ]
    lines += [_md(t), ""]
    lines += ["## Quintiles (monthly) and long-only top quintile", ""]
    rows = []
    for key, q in s["quintiles"].items():
        lo = s["long_only"][key]["base"]
        rows.append(
            {
                "series": key,
                "monotonicity": q["monotonicity"],
                "Q5-Q1 monthly": q["ls_mean_monthly_gross"],
                "LS net Sharpe": q["ls_net"].get("sharpe", float("nan")),
                "Q5 vs TR ann": lo["hedged_tr"].get("ann_return", float("nan")),
                "Q5 vs TR IR": lo["hedged_tr"].get("sharpe", float("nan")),
                "Q5 vs TR maxDD": lo["hedged_tr"].get("max_drawdown", float("nan")),
                "turnover 2-way": lo["turnover_two_way_monthly"],
            }
        )
    lines += [_md(pd.DataFrame(rows)), ""]
    lines += ["## Primary per year (h = 20)", "", _md(pd.DataFrame(s["primary_per_year"])), ""]
    lines += [
        "## Signal correlation (neutral, mean daily Spearman)",
        "",
        _md(corr["neutral"].reset_index().rename(columns={"index": "signal"})),
        "",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")

    fig, ax = plt.subplots(figsize=(7, 3.6))
    for name in ("REV20", "LOWTURN20", "RxT"):
        ic = series.get((name, pkey[1], pkey[2]))
        if ic is not None:
            ic.dropna().cumsum().plot(ax=ax, label=name)
    ax.set_title(f"Cumulative daily Rank IC ({pkey[1]}, h = {pkey[2]})")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "figures" / "cumulative_ic.png")
    plt.close(fig)

    q = pd.concat(quint_rows)
    q = q[(q.signal == pkey[0]) & (q.version == pkey[1])]
    fig, ax = plt.subplots(figsize=(5, 3.2))
    q.groupby("bucket").ret.mean().mul(100).plot.bar(ax=ax, color="0.35")
    ax.set_ylabel("mean monthly return, %")
    ax.set_title(f"{pkey[0]} ({pkey[1]}) quintiles")
    fig.tight_layout()
    fig.savefig(out / "figures" / "quintiles.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.6))
    for col, lab in (("long_base", "Q5 - CSI 500 TR, 20 bps"), ("long_stress", "40 bps")):
        ex = (daily[col] - daily["tr_ret"]).dropna()
        (1 + ex).cumprod().plot(ax=ax, label=lab)
    ax.axhline(1, color="k", lw=0.6)
    ax.set_title("Top-quintile long, hedged with CSI 500 total return (net)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "figures" / "hedged_nav.png")
    plt.close(fig)
