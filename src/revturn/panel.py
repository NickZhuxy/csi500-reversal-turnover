"""Turn raw vendor files into a typed, calendar-aligned panel (dates x codes).

Nothing here looks forward in time except `entry` fields, which describe the next session's open
and are named so. Raw files are read as strings and never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PRICE_FIELDS = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn"]
CHINEXT_20PCT_FROM = pd.Timestamp("2020-08-24")
MAIN_BOARD_ST_10PCT_FROM = pd.Timestamp("2026-07-06")  # SSE/SZSE trading rules revised 2026-04-24


@dataclass
class Panel:
    """Wide frames share one index (exchange trading days) and one column set (codes)."""

    fields: dict[str, pd.DataFrame]
    benchmarks: pd.DataFrame  # columns: tr_close, px_open, px_close, fut_ret (daily, aligned)
    meta: dict

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.fields[name]

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.fields["close"].index

    @property
    def codes(self) -> pd.Index:
        return self.fields["close"].columns


# ---------------------------------------------------------------- pure helpers (unit tested)


def adjusted_index(close: pd.DataFrame, preclose: pd.DataFrame) -> pd.DataFrame:
    """Total-return price index from raw bars: A_t = prod(close_s / preclose_s).

    `preclose` is the exchange's ex-rights reference price, so the ratio carries dividends and
    splits. This is BaoStock's own percent-change back-adjustment, up to a constant per code.
    """
    ratio = (close / preclose).where(close.notna() & preclose.notna() & (preclose > 0))
    idx = ratio.fillna(1.0).cumprod()
    return idx.where(close.notna())


def round_half_up(x: pd.DataFrame | np.ndarray, decimals: int = 2):
    scale = 10.0**decimals
    return np.floor(x * scale + 0.5 + 1e-9) / scale


def limit_pct(codes: pd.Index, dates: pd.DatetimeIndex, is_st: pd.DataFrame) -> pd.DataFrame:
    """Daily price-limit fraction per code and date (A-share rules, CSI 500 boards only).

    Main board 10%; STAR 20%; ChiNext 20% from 2020-08-24, 10% before. ST names: 5% on the main
    board until 2026-07-06 (10% after) and on ChiNext before its 2020 reform; STAR ST stays 20%.
    """
    code = pd.Series(codes, index=codes)
    star = (code.str.startswith("sh.688") | code.str.startswith("sh.689")).to_numpy()
    chinext = code.str.startswith("sz.30").to_numpy()
    main = ~star & ~chinext
    base = pd.DataFrame(0.10, index=dates, columns=codes)
    base.loc[:, star] = 0.20
    late = dates >= CHINEXT_20PCT_FROM
    base.loc[late, chinext] = 0.20
    st = (is_st == 1).reindex(index=dates, columns=codes, fill_value=False).to_numpy()
    five = np.zeros(base.shape, dtype=bool)
    five[np.ix_(dates < MAIN_BOARD_ST_10PCT_FROM, main)] = True
    five[np.ix_(~late, chinext)] = True
    return base.mask(st & five, 0.05)


def limit_flags(
    open_: pd.DataFrame, preclose: pd.DataFrame, pct: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """True where the session opened exactly at its limit-up / limit-down price."""
    up = round_half_up(preclose * (1 + pct))
    down = round_half_up(preclose * (1 - pct))
    at_up = (open_ >= up - 1e-6) & open_.notna()
    at_down = (open_ <= down + 1e-6) & open_.notna()
    return at_up, at_down


def asof_membership(
    snapshots: pd.DataFrame, dates: pd.DatetimeIndex, codes: pd.Index
) -> pd.DataFrame:
    """member[t, code] is True when code is in the latest snapshot dated on or before t.

    `snapshots` has columns updateDate (snapshot content date) and code. Snapshots repeated by
    several weekly queries are collapsed, so a stale vendor period stays stale.
    """
    snap = snapshots[["updateDate", "code"]].drop_duplicates()
    snap = snap.assign(updateDate=pd.to_datetime(snap.updateDate), v=True)
    wide = snap.pivot_table(index="updateDate", columns="code", values="v", aggfunc="any")
    wide = wide.reindex(columns=codes).astype("boolean").fillna(False).astype(bool)
    pos = wide.index.searchsorted(dates, side="right") - 1
    out = np.zeros((len(dates), len(codes)), dtype=bool)
    ok = pos >= 0
    out[ok] = wide.to_numpy()[pos[ok]]
    return pd.DataFrame(out, index=dates, columns=codes)


def asof_industry(hist: pd.DataFrame, dates: pd.DatetimeIndex, codes: pd.Index) -> pd.DataFrame:
    """Shenwan level-1 group (first two code digits) effective on each date; None if unknown.

    `hist` has columns symbol (six digits), start_date, industry_code, update_time.
    """
    h = hist.copy()
    h["start_date"] = pd.to_datetime(h.start_date)
    h = h.sort_values(["symbol", "start_date", "update_time"])
    h = h.drop_duplicates(["symbol", "start_date"], keep="last")
    h["l1"] = h.industry_code.astype(str).str[:2]
    by_symbol = dict(tuple(h.groupby("symbol")))
    out = pd.DataFrame(None, index=dates, columns=codes, dtype=object)
    for code in codes:
        rows = by_symbol.get(code[3:])
        if rows is None:
            continue
        pos = rows.start_date.searchsorted(dates, side="right") - 1
        vals = np.where(pos >= 0, rows.l1.to_numpy()[np.clip(pos, 0, None)], None)
        out[code] = vals
    return out


def listed_days(ipo: pd.Series, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Trading days elapsed since the first session on or after the IPO date."""
    first = dates.searchsorted(pd.to_datetime(ipo).to_numpy())
    rows = np.arange(len(dates))[:, None]
    return pd.DataFrame(rows - first[None, :], index=dates, columns=ipo.index)


def float_cap(close: pd.DataFrame, volume: pd.DataFrame, turn: pd.DataFrame) -> pd.DataFrame:
    """Tradable-share market cap implied by turnover: close * volume / (turn / 100)."""
    cap = close * volume / (turn / 100.0)
    cap = cap.where((turn > 0) & (volume > 0))
    return cap.ffill(limit=60)


def third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year=year, month=month, day=1)
    offset = (4 - first.weekday()) % 7
    return first + pd.Timedelta(days=offset + 14)


def expiry_session(year: int, month: int, dates: pd.DatetimeIndex) -> pd.Timestamp:
    """IC expiry: third Friday of the contract month, moved to the next session if a holiday."""
    e = third_friday(year, month)
    i = dates.searchsorted(e)
    return dates[i] if i < len(dates) else e


def front_month_symbol(t: pd.Timestamp, dates: pd.DatetimeIndex) -> str:
    """The contract a front-month holder owns over the session ending at t's close."""
    y, m = t.year, t.month
    if t > expiry_session(y, m, dates):
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"IC{y % 100:02d}{m:02d}"


def front_month_returns(contracts: dict[str, pd.DataFrame], dates: pd.DatetimeIndex) -> pd.Series:
    """Daily close-to-close return of the front IC contract, rolled after each expiry close.

    The expiring contract is held through its last session; the next day's return is measured on
    the new front month from the expiry close. Days whose front contract has no data are NaN.
    """
    closes = {s: c.drop_duplicates("date").set_index("date")["close"] for s, c in contracts.items()}
    out = pd.Series(np.nan, index=dates)
    for i in range(1, len(dates)):
        sym = front_month_symbol(dates[i], dates)
        c = closes.get(sym)
        if c is None:
            continue
        now, prev = c.get(dates[i]), c.get(dates[i - 1])
        if now is not None and prev is not None:
            out.iloc[i] = now / prev - 1.0
    return out


# ---------------------------------------------------------------- build from raw files


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def load_kdata(kdir: Path) -> pd.DataFrame:
    frames = [_read(p) for p in sorted(kdir.glob("*.parquet"))]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df.date)
    for c in PRICE_FIELDS:
        df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="coerce")
    for c in ("tradestatus", "isST"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int8")
    return df


def build_panel(raw: Path, start: str = "2016-07-01", end: str = "2026-10-26") -> Panel:
    bs_dir, ak_dir = raw / "baostock", raw / "akshare"
    cal = _read(bs_dir / "trade_dates.parquet")
    cal = pd.to_datetime(cal.loc[cal.is_trading_day == "1", "calendar_date"])
    dates = pd.DatetimeIndex(cal[(cal >= start) & (cal <= end)].sort_values().to_numpy())

    k = load_kdata(bs_dir / "kdata")
    codes = pd.Index(sorted(k.code.unique()))

    def wide(col: str) -> pd.DataFrame:
        w = k.pivot(index="date", columns="code", values=col)
        return w.reindex(index=dates, columns=codes)

    f = {c: wide(c).astype(float) for c in PRICE_FIELDS}
    f["tradestatus"] = wide("tradestatus").astype(float)
    f["is_st"] = wide("isST").astype(float)
    f["has_row"] = f["close"].notna()
    f["adj_close"] = adjusted_index(f["close"], f["preclose"])
    f["adj_open"] = f["adj_close"] * f["open"] / f["close"]
    pct = limit_pct(codes, dates, f["is_st"])
    f["open_limit_up"], f["open_limit_down"] = limit_flags(f["open"], f["preclose"], pct)

    mem = pd.concat([_read(p) for p in sorted((bs_dir / "membership").glob("*.parquet"))])
    f["member"] = asof_membership(mem, dates, codes)

    basic = _read(bs_dir / "stock_basic.parquet").set_index("code")
    ipo = basic.ipoDate.reindex(codes)
    f["listed_days"] = listed_days(ipo, dates).astype(float)
    f["float_cap"] = float_cap(f["close"], f["volume"], f["turn"])
    f["industry"] = asof_industry(_read(ak_dir / "sw_industry_hist.parquet"), dates, codes)

    bench = load_benchmarks(bs_dir, ak_dir, dates)
    meta = {
        "dates": [str(dates[0].date()), str(dates[-1].date())],
        "n_dates": len(dates),
        "n_codes": len(codes),
        "kdata_rows": int(len(k)),
        "membership_snapshots": int(mem.updateDate.nunique()),
        "out_dates": basic.outDate.reindex(codes).replace("", np.nan).dropna().to_dict(),
    }
    return Panel(fields=f, benchmarks=bench, meta=meta)


def _csindex(path: Path) -> pd.DataFrame:
    """CSIndex daily file; it carries rows on some non-trading days, dropped by reindexing."""
    d = _read(path)
    d = d.assign(
        date=pd.to_datetime(d["日期"]),
        open=pd.to_numeric(d["开盘"], errors="coerce"),
        close=pd.to_numeric(d["收盘"]),
    )
    return d.drop_duplicates("date", keep="last").set_index("date")


def load_benchmarks(bs_dir: Path, ak_dir: Path, dates: pd.DatetimeIndex) -> pd.DataFrame:
    tr = _csindex(ak_dir / "index_H00905_csindex.parquet")["close"]
    px = _csindex(ak_dir / "index_000905_csindex.parquet")
    fut_dir = ak_dir / "futures"
    contracts = {}
    for p in sorted(fut_dir.glob("IC[0-9][0-9][0-9][0-9].parquet")):
        c = _read(p)
        c = c.assign(date=pd.to_datetime(c.date), close=pd.to_numeric(c.close))
        contracts[p.stem] = c[["date", "close"]]
    out = pd.DataFrame(index=dates)
    out["tr_close"] = tr.reindex(dates)
    out["px_open"] = px["open"].reindex(dates)
    out["px_close"] = px["close"].reindex(dates)
    out["fut_ret"] = front_month_returns(contracts, dates) if contracts else np.nan
    return out


# ---------------------------------------------------------------- persistence

_BOOL = {"has_row", "open_limit_up", "open_limit_down", "member"}


def save_panel(panel: Panel, out: Path) -> None:
    """Store each field long-form (observed cells only) in one Parquet file."""
    out.mkdir(parents=True, exist_ok=True)
    rows = panel.fields["has_row"].stack()
    cells = rows[rows.astype(bool)].index  # observed (date, code) cells only
    cols = {}
    for k, v in panel.fields.items():
        if k == "member":
            continue  # stored separately: a member can lack a bar
        s = v.stack().reindex(cells)
        cols[k] = s.astype(bool) if k in _BOOL else s
    long = pd.DataFrame(cols, index=cells)
    long.index.names = ["date", "code"]
    long["industry"] = long["industry"].astype("string")
    long.reset_index().to_parquet(out / "panel.parquet", index=False)
    panel.benchmarks.rename_axis("date").reset_index().to_parquet(out / "benchmarks.parquet")
    pd.Series(panel.meta, dtype=object).to_json(out / "panel_meta.json", default_handler=str)
    member = panel.fields["member"].stack()
    member.index.names = ["date", "code"]
    member[member.astype(bool)].reset_index()[["date", "code"]].to_parquet(out / "member.parquet")


def load_panel(out: Path) -> Panel:
    long = pd.read_parquet(out / "panel.parquet")
    bench = pd.read_parquet(out / "benchmarks.parquet").set_index("date")
    dates = pd.DatetimeIndex(bench.index)
    codes = pd.Index(sorted(long.code.unique()))
    long = long.set_index(["date", "code"])
    f = {}
    for col in long.columns:
        w = long[col].unstack("code").reindex(index=dates, columns=codes)
        if col in _BOOL:
            w = w.astype("boolean").fillna(False).astype(bool)
        elif col == "industry":
            w = w.astype(object).where(w.notna(), None)
        else:
            w = w.astype(float)
        f[col] = w
    mem = pd.read_parquet(out / "member.parquet")
    mem["v"] = True
    f["member"] = (
        mem.pivot_table(index="date", columns="code", values="v", aggfunc="any")
        .reindex(index=dates, columns=codes)
        .astype("boolean")
        .fillna(False)
        .astype(bool)
    )
    meta = pd.read_json(out / "panel_meta.json", typ="series").to_dict()
    return Panel(fields=f, benchmarks=bench, meta=meta)
