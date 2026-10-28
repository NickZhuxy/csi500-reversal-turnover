"""Raw downloads from public sources. Files are stored as vendor strings and never edited.

BaoStock rules (https://www.baostock.com/blacklist, read 2026-10-27): at most 50,000 requests a
day and no concurrent connections. This module keeps exactly one BaoStock session open. No
account, key, or payment is used; `bs.login()` is the library's anonymous default.
"""

from __future__ import annotations

import datetime as dt
import json
import signal
import time
from pathlib import Path

import pandas as pd

MEMBER_START = "2017-09-04"
END = "2026-10-26"
KFIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
)
PAD_BEFORE_DAYS = 75  # calendar days before first membership: lookback and listing checks
PAD_AFTER_DAYS = 100  # calendar days after last membership: labels and held positions
INDEXES = ["sh.000905", "sh.000300", "sh.000852", "sh.000001"]


class _Log:
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, event: str, **kw) -> None:
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "event": event, **kw}
        with self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps(rec, ensure_ascii=False), flush=True)


def _save(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    df.astype("string").to_parquet(tmp, index=False)
    tmp.rename(path)


class PartialResult(RuntimeError):
    """A BaoStock result set reported an error part-way through its pages."""


def drain(rs) -> pd.DataFrame:
    """Read every row of a BaoStock result set, or raise if any page failed.

    `rs.next()` only advances after `rs.get_row_data()`, so both are called each step. Parallel
    sessions were seen to end a result early (2,000 of 2,504 rows); the error code is checked
    after the loop so that case raises instead of returning a short table.
    """
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise PartialResult(f"error {rs.error_code} ({rs.error_msg}) after {len(rows)} rows")
    return pd.DataFrame(rows, columns=rs.fields, dtype="string")


def download_baostock(out: Path) -> None:
    import baostock as bs

    out.mkdir(parents=True, exist_ok=True)
    for sub in ("kdata", "index", "kdata_adj_check", "membership"):
        (out / sub).mkdir(exist_ok=True)
    log = _Log(out / "download_log.jsonl")
    requests = {"n": 0}

    def login() -> None:
        for attempt in range(5):
            lg = bs.login()
            if lg.error_code == "0":
                return
            log("login_failed", code=lg.error_code, msg=lg.error_msg, attempt=attempt)
            if lg.error_code == "10001011":
                raise SystemExit("BaoStock blacklist response; stopping")
            time.sleep(30 * (attempt + 1))
        raise SystemExit("BaoStock login failed")

    def fetch(fn, *args, **kwargs) -> pd.DataFrame:
        """A page error is retried with a fresh session, never returned as a short table."""
        for attempt in range(4):
            requests["n"] += 1
            try:
                return drain(fn(*args, **kwargs))
            except PartialResult as e:
                log(
                    "query_error",
                    fn=fn.__name__,
                    args=str(args)[:80],
                    error=str(e),
                    attempt=attempt,
                )
            time.sleep(10 * (attempt + 1))
            bs.logout()
            login()
        raise RuntimeError(f"{fn.__name__}{args} failed after retries")

    log("start", baostock_version=getattr(bs, "__version__", "?"))
    login()
    cal_path = out / "trade_dates.parquet"
    if not cal_path.exists():
        _save(fetch(bs.query_trade_dates, start_date="2015-01-01", end_date="2026-12-31"), cal_path)
    cal = pd.read_parquet(cal_path)
    if not (out / "stock_basic.parquet").exists():
        _save(fetch(bs.query_stock_basic), out / "stock_basic.parquet")

    days = pd.to_datetime(cal.loc[cal.is_trading_day == "1", "calendar_date"])
    days = days[(days >= MEMBER_START) & (days <= END)]
    weekly = days.groupby(days.dt.strftime("%G-%V")).min()
    for d in weekly.dt.strftime("%Y-%m-%d"):
        p = out / "membership" / f"{d}.parquet"
        if not p.exists():
            df = fetch(bs.query_zz500_stocks, date=d)
            df.insert(0, "query_date", d)
            _save(df, p)
    mem = pd.concat([pd.read_parquet(p) for p in sorted((out / "membership").glob("*.parquet"))])

    spans = mem.groupby("code").query_date.agg(["min", "max"])
    for i, (code, row) in enumerate(spans.iterrows()):
        p = out / "kdata" / f"{code}.parquet"
        if p.exists():
            continue
        start = pd.Timestamp(row["min"]) - pd.Timedelta(days=PAD_BEFORE_DAYS)
        end = min(pd.Timestamp(row["max"]) + pd.Timedelta(days=PAD_AFTER_DAYS), pd.Timestamp(END))
        df = fetch(
            bs.query_history_k_data_plus,
            code,
            KFIELDS,
            start_date=f"{start:%Y-%m-%d}",
            end_date=f"{end:%Y-%m-%d}",
            frequency="d",
            adjustflag="3",
        )
        if df.date.duplicated().any():
            raise RuntimeError(f"duplicate dates for {code}")
        _save(df, p)
        if i % 25 == 0:
            log(
                "kdata_progress",
                i=i,
                of=len(spans),
                code=code,
                rows=len(df),
                requests=requests["n"],
            )

    for code in INDEXES:
        p = out / "index" / f"{code}.parquet"
        if not p.exists():
            _save(
                fetch(
                    bs.query_history_k_data_plus,
                    code,
                    "date,code,open,high,low,close,preclose,volume,amount,pctChg",
                    start_date="2016-01-01",
                    end_date=END,
                    frequency="d",
                    adjustflag="3",
                ),
                p,
            )
    for code in sorted(spans.index)[::60][:22]:  # vendor back-adjusted check sample
        p = out / "kdata_adj_check" / f"{code}.parquet"
        if not p.exists():
            _save(
                fetch(
                    bs.query_history_k_data_plus,
                    code,
                    "date,code,open,close,adjustflag",
                    start_date="2016-07-01",
                    end_date=END,
                    frequency="d",
                    adjustflag="1",
                ),
                p,
            )
    bs.logout()
    log("done", requests=requests["n"])


def _alarm(signum, frame):
    raise TimeoutError("request timed out")


def download_akshare(out: Path, timeout: int = 60) -> None:
    import akshare as ak

    (out / "futures").mkdir(parents=True, exist_ok=True)
    log = _Log(out / "download_log.jsonl")
    signal.signal(signal.SIGALRM, _alarm)
    log("start", akshare_version=ak.__version__)
    jobs = {
        "sw_industry_hist.parquet": lambda: ak.stock_industry_clf_hist_sw(),
        "index_H00905_csindex.parquet": lambda: ak.stock_zh_index_hist_csindex(
            symbol="H00905", start_date="20160101", end_date=END.replace("-", "")
        ),
        "index_000905_csindex.parquet": lambda: ak.stock_zh_index_hist_csindex(
            symbol="000905", start_date="20160101", end_date=END.replace("-", "")
        ),
        "futures/IC0_main_sina.parquet": lambda: ak.futures_main_sina(
            symbol="IC0", start_date="20160101", end_date=END.replace("-", "")
        ),
    }
    for name, fn in jobs.items():
        p = out / name
        if not p.exists():
            df = fn()
            _save(df, p)
            log("saved", file=name, rows=len(df))
    for year in range(2018, 2027):
        for month in range(1, 13):
            sym = f"IC{year % 100:02d}{month:02d}"
            p = out / "futures" / f"{sym}.parquet"
            if p.exists():
                continue
            signal.alarm(timeout)  # Sina sometimes never answers
            try:
                df = ak.futures_zh_daily_sina(symbol=sym)
            except Exception as e:  # contracts before late 2018 are not served by Sina
                log("contract_missing", symbol=sym, error=type(e).__name__)
                continue
            finally:
                signal.alarm(0)
            df.insert(0, "symbol", sym)
            _save(df, p)
            time.sleep(0.5)
    log("done")


def manifest(raw: Path) -> dict:
    """Row counts and SHA-256 per raw file, for the data profile and run provenance."""
    from .freeze import sha256_file

    files = sorted(p for p in raw.rglob("*.parquet"))
    rec = {
        str(p.relative_to(raw)): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
        for p in files
    }
    return {"n_files": len(files), "bytes": sum(v["bytes"] for v in rec.values()), "files": rec}
