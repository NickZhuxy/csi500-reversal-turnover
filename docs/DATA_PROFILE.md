# Data profile

What data the study uses, how large it is, what went wrong while collecting and processing it, and
which distributions were surprising. Every number here comes from `docs/data_profile.json`
(written by `scripts/profile_data.py`), from the download logs in `data/raw/*/download_log.jsonl`,
or from a run directory named in the text. Downloaded on **2026-10-27** with BaoStock 0.9.4,
AKShare 1.20.1, and pandas 3.1.0 on an Apple-silicon Mac with 16 GB of memory.

## 1. Sources and field definitions

| Dataset | Vendor and call | Coverage | Notes |
|---|---|---|---|
| Daily stock bars | BaoStock `query_history_k_data_plus(code, ..., adjustflag="3")` | 1,216 codes, 2017-06-21 to 2026-10-26 | Unadjusted. One call per code, limited to its CSI 500 window plus padding. |
| CSI 500 membership | BaoStock `query_zz500_stocks(date)` | 468 weekly queries from 2017-09-04; 388 distinct snapshots | The vendor updates every Monday. |
| Listing dates | BaoStock `query_stock_basic` | 9,022 securities | `ipoDate`, `outDate` (59 universe codes delisted). |
| Trading calendar | BaoStock `query_trade_dates` | 2015-2026 | |
| Industry | Shenwan via AKShare `stock_industry_clf_hist_sw` | 12,929 records, 5,934 symbols | Start date per classification; SW2014 codes, SW2021 from 2021-07-30. |
| Benchmarks | CSIndex via AKShare `stock_zh_index_hist_csindex` | H00905 total return, 000905 price | Cross-checked: BaoStock and CSIndex both give 000905 = 6,712.73 on 2026-10-26. |
| Index futures | Sina via AKShare `futures_zh_daily_sina(ICyymm)` | 94 contracts, usable from 2019-04-22 | Plus the main continuous series `IC0` for basis levels. |

BaoStock daily fields (from its documentation, read 2026-10-27):

| Field | Definition |
|---|---|
| `open`, `high`, `low`, `close` | Prices in CNY, 4 decimals, unadjusted. |
| `preclose` | Exchange reference price for the day: the previous close, or the ex-rights price on an ex-date. |
| `volume`, `amount` | Shares and CNY traded. |
| `turn` | `volume / tradable A shares * 100`, in percent, 6 decimals. Empty on suspended days. |
| `tradestatus` | 1 traded, 0 suspended. Suspended bars have OHLC = previous close and volume 0. |
| `isST` | 1 if the name carries an ST or *ST flag that day. |

**Adjustment.** Prices are adjusted locally as `A_t = prod(close_s / preclose_s)`, which is BaoStock's
own "percent-change" back-adjustment. A check against BaoStock's back-adjusted series
(`adjustflag="1"`) for 21 codes found a worst daily-return difference of 7.7e-7 and a worst
end-to-end difference of 6.6e-7, which is rounding error.

**Access.** BaoStock needs no account; `bs.login()` uses the library's anonymous default. Its rules
allow 50,000 requests a day and no concurrent connections. The full download used 1,711 requests
over 2 h 25 min (membership 24 min, bars about 2 h). AKShare wraps public websites; its
documentation describes the data as for academic research.

## 2. Scale

| Item | Value |
|---|---|
| Raw files | 1,809 Parquet files, 98.1 MB |
| Stock-day bars | **1,322,839 rows x 14 fields**; 89.1 MB as raw Parquet; 167 MB typed in memory |
| Panel | 2,504 sessions x 1,216 codes x 19 fields; 462 MB as wide frames in memory; 73 MB as long Parquet |
| Filled share of the wide grid | 43%: most codes are outside the index for most of the sample |
| Dev universe | 849,478 member-days, 2018-2024; mean 500 members, 494 eligible, 492 eligible and tradable a day |
| Peak resident memory | build 1.83 GB; one full run 1.32 GB |
| Run time | build 9.7 s; one split (30 ICs, 10 x 2 quintile and long-short sets, 20 long-only simulations, diagnostics) about 60 s |

The data fits in memory on a laptop, so the cost questions were download time and per-day loops
rather than distributed processing.

## 3. How processing cost was reduced (measured)

| Step | Before | After | How |
|---|---|---|---|
| Download | 2.77 M rows if every code were fetched from listing (or 2016-07) to 2026-10 | 1.32 M rows fetched | Fetch each code only from 75 days before its first membership week to 100 days after its last. At the observed 5.4 s per 1,000 rows this saved about 2.2 hours. |
| Download route | Bulk per-date endpoint: about 40 s per day, about 28 h for 2,504 days | Per-code endpoint, about 2 h | Measured on three dates before choosing. |
| Reading raw data | 1,216 small files: 1.51 s | One consolidated Parquet: 0.07 s | `revturn build` consolidates once (9.7 s); later runs load the panel in 2.1 s. |
| Storage format | CSV 121.7 MB, write 3.78 s | Parquet 46.7 MB, write 0.41 s | Reads were similar here (0.09 s vs 0.05 s); Parquet also keeps types and reads 4 columns in 0.01 s. |
| Daily Rank IC | `scipy.stats.spearmanr` per day: 1.94 s for 2,214 days (extrapolated from 250) | Vectorized ranks + row-wise correlation: 0.18 s | About 10x; matches scipy to machine precision in a test with NaNs and ties. |
| Neutralization | pandas `get_dummies` per day: 1.61 s per signal | numpy integer one-hot: 0.67 s per signal | 2.4x; identical output (max difference 1.3e-15). All 10 series now take 4.0 s; the test suite went from 17 s to 8 s. |
| Memory | 167 MB typed bars | 148 MB with `code` as category; 101 MB with float32 | float32 was not adopted: `amount` needs more than 7 significant digits, and limit prices need exact cents. |

## 4. Problems met while collecting and processing

1. **Parallel BaoStock sessions truncate silently.** A speed test with 4 and 8 sessions returned
   exactly 2,000 of 2,504 rows for several codes, with no error code. The test also broke the
   vendor's no-concurrency rule; it is recorded in `EXPERIMENT_LOG.md` (E0). The downloader is serial
   and `download.drain` raises on any page error instead of returning a short table (tested).
2. **BaoStock's cursor only advances on `get_row_data()`.** `while rs.next(): n += 1` loops forever.
   BaoStock's own `get_data()` uses `DataFrame.append`, removed in pandas 2, so it fails on pandas 3.
3. **Late membership snapshots.** Three semiannual rebalances appear late in BaoStock: 2021-06-15
   (shown 2021-07-19), 2021-12-13 (2022-01-17), and 2022-12-12 (2023-03-20, 14 weeks). In each window
   50 of 500 names are wrong. Found by listing snapshot change dates before any result; handled by
   robustness checks R3 and R3b (dropping them moves the dev primary IC from 0.0190 to 0.0194 and
   0.0210).
4. **Vendor rows that break the vendor's own rules.** `sh.688065` on 2023-06-15 has
   `tradestatus = 0` but traded 353,426 shares and its OHLC differ from `preclose`. 18 suspended rows
   (0.16%, 4 codes) carry a turnover value instead of an empty one: 16 are `0`, two are positive.
5. **CSIndex daily files include non-trading days** (for example 2018-01-01, a copy of 2018-01-02).
   All series are reindexed to the exchange calendar.
6. **Futures data gaps.** Sina serves no history for IC1812, IC1901, or IC1902; IC1903 has one row and
   IC1904 two. The futures hedge therefore starts on 2019-04-22. Some Sina requests never answered,
   so each call has a 60-second alarm. CFFEX's own endpoint took 76 s for one date.
7. **Precision.** Eastmoney's daily bars (via AKShare) round turnover to 0.01 percentage points, too
   coarse for the 1% of traded rows below 0.14%; BaoStock's 6-decimal `turn` was used instead.
8. **pandas 3 behaviour changes.** `pct_change` no longer fills gaps (one missing index close would
   drop two days from a hedged series), `stack` keeps NaN cells, and `None` in an object column can
   become NaN. Each was found in testing and handled explicitly.
9. **Price-limit rules change over time.** ChiNext moved to 20% on 2020-08-24, and main-board ST names
   moved from 5% to 10% on 2026-07-06. Both are encoded in `panel.limit_pct` and tested.
10. **iCloud Drive.** The environment lives outside the synced folder (see `LOCAL_SETUP.md`). The
    project folder is not pinned with "Keep Downloaded", so `data/` can be evicted.

## 5. Unexpected distributions and their causes

**Turnover is heavily right-skewed.** Among traded rows the median is 1.03% a day, the 99th
percentile 10.8%, the 99.9th 21.5%, and the maximum 73.2% (`sh.600956` on 2021-03-31, inside a run
of limit-up and large down days). Winsorizing at 5 MAD and ranking are needed before turnover
enters a product, or a few names would set the whole cross-section.

**Near-zero turnover is concentrated, not random.** 481 traded rows have turnover below 0.05%. Two
causes show up in the rows:

- *Limit-locked days.* `sz.000939` in July 2018 fell about 4.9% (the ST limit) on consecutive days
  with turnover as low as 0.0007%: sellers queued at the limit and almost nothing traded.
- *Rarely-traded "tradable" shares.* The most frequent names (`sh.600578` 110 rows, `sh.600317` 41,
  `sh.600098` 37) are utility and port operators. A likely cause is large holdings that count as
  tradable in the turnover denominator but seldom trade; the data cannot confirm the holders.

**Zero values.** No traded row has zero volume or zero turnover; zeros and empties occur only on
suspended days (0.83% of rows, 6,872 member-days in dev).

**Price moves are capped by the limits.** The 0.01% and 99.99% quantiles of daily returns are -15.7%
and +20.0% (the ChiNext and STAR limits). One row exceeds 21%: `sh.601868` rose 40% on 2021-09-28,
its first session after listing by absorbing `sh.600068`, which delisted on 2021-09-13.

**Limit and suspension events in the dev universe** (849,478 member-days): 1,240 opens at limit-up,
1,247 at limit-down, 1,129 one-price bars (high = low), 6,872 suspended days, and 1,073 ST days
(names that became ST while still in the index). Each is small, but limit opens cluster in the
names a reversal signal ranks at the extremes, so the simulator counted 50 blocked buys and 127
blocked sells for the primary over 83 rebalances.

**Tradable-share counts jump.** The share count implied by `volume / turn` rises by more than 50% in
one day on 298 member-days; 288 of these are increases (median x2.2, up to x10). These are lock-up
expiries. They move the size proxy and cut measured turnover mechanically on those days.

**Ex-rights days are frequent.** 5,197 rows have a `preclose` different from the previous close
(dividends, bonus shares, splits). Unadjusted closes would show a false drop on each.

**Index futures traded at a persistent discount.** The IC main contract's average close-to-spot basis
by year ranged from -0.07% (2023) to -1.47% (2025). As a hedge cost this was 7.1% a year over
2019-04-22 to 2024-12-31 and 13.3% a year in 2025, measured as the price-index return minus the
front-month return on the same days.

## 6. What I would design differently for a database

1. **Bitemporal reference tables.** Index membership and industry need both an effective date and a
   recorded date. The three late membership windows come from treating a vendor's recording date as
   the effective date. Use the official CSIndex announcements for effective dates and keep the vendor
   snapshot only as a check.
2. **A security master.** Code changes, listing and delisting, board, and the price-limit regime as
   rows with validity dates, not as `if` statements in code.
3. **Corporate actions and share counts as tables.** Store ex-dates, cash dividends, split ratios, and
   total / tradable / free-float shares directly, instead of inferring them from `preclose` and
   `volume / turn`. Keep raw and adjusted prices side by side.
4. **A tradability state table.** Suspended, ST, limit-locked at the open, and one-price bar per
   (date, code), computed once at ingest and versioned, so research and simulation read the same
   flags.
5. **Data-quality checks at ingest**, as code that fails loudly: rows per date against the calendar,
   duplicate keys, `preclose` continuity outside ex-dates, vendor conventions such as suspended-bar
   prices (the `sh.688065` case), 500 members per snapshot, and cross-vendor index levels.
6. **Storage.** One long fact table partitioned by year and sorted by (date, code) in Parquet or
   DuckDB; wide matrices only as caches built for a research question. This project already keeps the
   raw layer immutable with a SHA-256 manifest (`data/raw/manifest.json`), which should stay.
