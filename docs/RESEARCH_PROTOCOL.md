# Research protocol — CSI 500 20-day reversal x turnover

Status: **approved by Nick Zhu on 2026-10-27 and frozen.** Written the same day, before any factor
value, forward return, IC, or portfolio return was computed. Up to approval, only data availability
was inspected: field definitions, row counts, download speed, and the dates on which BaoStock updated
its CSI 500 membership snapshots. Changes after approval are dated deviations in
[`EXPERIMENT_LOG.md`](EXPERIMENT_LOG.md), each with its reason, recorded before any out-of-sample
period is opened. The values in this file are mirrored in [`configs/default.json`](../configs/default.json).

This is an independent study on public data only. The author studied a factor of this kind before,
in earlier work on non-public data; no data, code, or output from that work is used here, and its
reported numbers are **not targets**. No choice below was made with them in mind, and the results are
reported whatever they are. (The public copy of this protocol words that earlier work neutrally; the
research content is identical to the version approved on 2026-10-27.)

## 1. Question and hypotheses

Does a 20-day price reversal signal, combined with turnover, predict the cross-section of next-month
returns among CSI 500 constituents, after realistic A-share trading constraints and costs? Is the
combination better than its two parts?

- **H1 (reversal).** Stocks with low 20-day returns outperform: `REV20` has a positive Rank IC.
- **H2 (low turnover).** Stocks with low 20-day average turnover outperform: `LOWTURN20` has a
  positive Rank IC.
- **H3 (interaction, primary).** Reversal is stronger among high-turnover stocks, because
  speculative, turnover-heavy moves overshoot more. `RxT` has a positive Rank IC that is at least as
  large as `REV20`'s.
- **H4 (additive composite).** An equal-weight sum of the two parts beats each part.

Prior expectation, written before results: H1 and H2 are well-documented A-share anomalies, but
several sell-side studies report that both weakened after 2017 as the market became more
institutional. I expect a positive but modest IC for H1 and H2 and have no strong prior on H3. High
signal turnover means costs will matter.

## 2. Data (public only)

| Item | Source | Notes |
|---|---|---|
| Daily stock bars, unadjusted | BaoStock `query_history_k_data_plus`, `adjustflag=3` | open, high, low, close, `preclose` (exchange ex-rights reference price), volume, amount, `turn` (%, volume / tradable A shares), `tradestatus`, `isST`. Suspended days are present, with OHLC equal to the previous close and empty `turn`. |
| CSI 500 membership | BaoStock `query_zz500_stocks(date)` | Weekly snapshots (updated each Monday). Queried on the first trading day of every week from 2017-09-04. |
| Listing dates | BaoStock `query_stock_basic` | `ipoDate`, `outDate`. |
| Trading calendar | BaoStock `query_trade_dates` | |
| Industry | AKShare `stock_industry_clf_hist_sw` (Shenwan) | Classification history with start dates. Level 1 = first two digits of the code. SW2014 codes until the 2021-07-30 revision, SW2021 after. |
| Benchmarks | CSIndex via AKShare: CSI 500 total return (H00905), CSI 500 price (000905) | |
| Index futures | Sina via AKShare `futures_zh_daily_sina` per IC contract | Served from contracts listed in late 2018, so the futures hedge starts 2019-01. |

Adjusted prices are rebuilt locally from raw data: `A_t = prod(close_s / preclose_s)`. Because
`preclose` is the exchange's ex-rights reference price, this is BaoStock's own "percent-change"
back-adjustment and includes cash dividends. Adjusted open is `A_t * open_t / close_t`. A fixed
sample of 22 stocks is also downloaded with BaoStock's `adjustflag=1` to check the rebuild.

Free-float size proxy: `float_cap = close * volume / (turn / 100)`, the tradable-share count implied
by BaoStock's own turnover definition, times the unadjusted close.

**Known data defect, found before any result.** BaoStock's CSI 500 snapshot did not update between
2022-08-01 and 2023-03-20, so the December 2022 rebalance (effective 2022-12-12) is missing for
14 weeks. The main run keeps this window. A pre-registered robustness check drops signal dates
2022-12-12 to 2023-03-17.

All downloads are serial. BaoStock's published rules allow at most 50,000 requests a day and no
concurrent connections. No account, key, or payment is used.

## 3. Sample and splits

| Split | Signal dates | Label windows end by | Opened |
|---|---|---|---|
| Development (dev) | 2018-01-02 to 2024-12-31 | 2024-12-31 | Freely, after this protocol is approved |
| Out-of-sample A (OOS-A) | 2025-01-02 to 2025-12-31 | 2025-12-31 | Once, after the freeze |
| Out-of-sample B (OOS-B) | 2026-01-05 to 2026-09-30 | 2026-10-26 | Once, after OOS-A is reported |

OOS-A is the first full year after dev. It is not strictly unseen: the earlier work mentioned above
covered part of 2025. OOS-B (2026) is the only period that no version of this study has seen. A signal date is dropped from a split when its label window
would cross the split's end, so no label leaks across a boundary.

**Freeze guard.** The CLI refuses an OOS run unless `configs/freeze.json` exists and its SHA-256
hashes of the config and of `src/` match the current files. The freeze is written only after the dev
run is reported and logged.

## 4. Universe (evaluated at each signal close t)

A stock is eligible on day t when all hold:

1. It is in the latest CSI 500 snapshot dated on or before t.
2. `isST == 0` on t.
3. `tradestatus == 1` on t.
4. At least 60 trading days have passed since `ipoDate`.
5. It traded on at least 15 of the last 20 trading days.

**Tradable at entry (t+1 open).** For the IC and the quintile research, a name is also dropped when it
is suspended at t+1 or opens at its limit-up or limit-down price. This uses only what is known at the
t+1 open, which is when the trade happens. Limit prices are `round(preclose * (1 +/- p), 2)` with
p = 10% for the main boards, 20% for ChiNext from 2020-08-24 (10% before), and 20% for STAR. ST names
are already excluded.

## 5. Signals (five, fixed)

Inputs at close t: `r20 = A_t / A_{t-20} - 1` over 20 exchange trading days, and `turn20` = the mean of
daily `turn` over traded days in the last 20 trading days. Every signal is oriented so that higher
means a higher predicted return.

| ID | Definition | Role |
|---|---|---|
| `REV20` | `-r20` | H1 component |
| `LOWTURN20` | `-turn20` | H2 component |
| `RxT` | `-(r20 - median_t(r20)) * turn20` | **Primary.** Literal "reversal x turnover": the cross-sectionally demeaned reversal, scaled by turnover. |
| `RxT_RANK` | `(rank_t(-r20) - 0.5) * rank_t(turn20)` | Rank-product version of `RxT`, robust to outliers (`rank_t` is the percentile rank in (0, 1]). |
| `ADD` | `z(REV20) + z(LOWTURN20)`, then z-scored again | H4 additive composite |

**Processing per day, over eligible names only:**

1. Winsorize: clip at median +/- 5 x MAD, where MAD = median(|x - median|).
2. Z-score: subtract the mean, divide by the standard deviation.
3. Neutralized version: OLS of the z-score on Shenwan level-1 dummies plus z(log float_cap); take the
   residual and z-score it again. Industries with fewer than 5 eligible names that day are pooled
   into one group. For `ADD`, the final composite is neutralized, not its parts.

That makes 5 signals x 2 versions (raw, neutralized) = 10 signal series. The 20-day lookback is part
of the definition and is not varied.

## 6. Labels

`fwd_h(t) = Aopen_{t+1+h} / Aopen_{t+1} - 1` for h in {1, 5, 20} trading days. Entry is at the next
open; the close-t to open-t+1 move cannot be traded and is excluded. If a stock is suspended on the
exit day, the label uses its last traded close, which is the price BaoStock fills on suspended days.
If a stock delists inside the window, the label uses its last available close, and the count of such
cases is reported.

## 7. Statistics (development split)

- **Daily Rank IC.** Spearman correlation between signal and label over eligible, tradable names
  with a label. A date needs at least 100 names.
- Reported per series and horizon: mean IC, IC standard deviation, ICIR = mean / std (daily,
  **not annualized**), the share of days with IC > 0, and a per-year table.
- **HAC t-statistic** of the mean IC: Newey-West, Bartlett kernel, lags = max(5, 2h), so 5, 10, and
  40. The doubled lag is conservative for overlapping labels.
- **Multiple testing.** The family is 10 series x 3 horizons = **30 tests**. Two-sided p-values,
  Benjamini-Hochberg q-values over all 30.
- **H3 comparison.** The mean of the daily IC difference `RxT - REV20` (neutralized, h = 20), with a
  HAC t-statistic. Reported alongside the family, not counted in it.
- **Correlation.** The average daily cross-sectional Spearman correlation between the five signals.

## 8. Portfolios

- **Rebalance.** Signal at the close of the last trading day of each month; trades at the next
  open; held until the next rebalance. Names are equal-weighted at rebalance, then drift.
- **Quintiles (research).** Five equal-weight quintiles of the neutralized primary signal, eligible
  and tradable names only. Reported: mean monthly return per quintile, and the monotonicity
  statistic = Spearman correlation between quintile number and mean return.
- **Long-short Q5 - Q1 (theoretical).** The daily return difference of the two drifting books; costs
  on both legs. A-share single-name shorting is limited, so this is a diagnostic, not a strategy.
- **Long-only Q5 with a hedge (realistic).** Simulated daily. A buy is blocked when the name opens at
  limit-up or is suspended; the remaining buys are reweighted. A sell is blocked when the name opens
  at limit-down or is suspended; the position is kept and the sell is retried at each next open.
  Three hedges, each a daily-rebalanced beta-1 short:
  1. **CSI 500 total return (H00905), the primary hedge.** Its dividends match the dividend-inclusive
     stock returns.
  2. CSI 500 price index (000905), to show the dividend effect.
  3. Front-month IC future, rolled at expiry, from 2019-01. It includes the basis cost, which was
     large and negative for IC for much of the sample.
- **Costs.** 20 bps one-way on traded notional is the baseline (all-in: commission, stamp duty,
  slippage); 40 bps is the stress case. Futures costs are ignored (under 1 bp a roll).
- **Metrics.** Annualized return (242 trading days), volatility, Sharpe (zero risk-free rate),
  information ratio of the hedged return, maximum drawdown, monthly hit rate, and monthly two-way
  turnover (buys plus sells as a fraction of NAV). Also reported: names held, and a capacity note (traded notional against 20-day average
  daily amount, at 5% participation).

## 9. Pass criteria (fixed now; evaluated on the dev split)

The primary hypothesis is **`RxT`, neutralized, h = 20**. It passes in dev only if all five hold:

| # | Criterion |
|---|---|
| P1 | Mean daily Rank IC >= 0.02 |
| P2 | HAC t >= 2.0, and BH q <= 0.05 within the 30-test family |
| P3 | Positive mean IC in at least 5 of the 7 dev years |
| P4 | Quintile monotonicity >= 0.9, and Q5 - Q1 mean return > 0 |
| P5 | Long-only Q5 hedged with H00905, net of 20 bps: annualized excess return > 0 and IR >= 0.5; and excess return still > 0 at 40 bps |

These are this project's own thresholds, modelled on common sell-side factor-screening practice. The other nine series are reported with the
same statistics. Their pass/fail is descriptive, and none of them can be promoted to primary after
the results are seen.

**OOS (A and B, each separately).** Pass if the primary's mean Rank IC (h = 20) is > 0 and the
H00905-hedged Q5 excess return is > 0 net of 20 bps. HAC t is reported, but there is no t threshold:
one year holds about 12 independent monthly observations, too few for a meaningful threshold.

## 10. Robustness (pre-registered, descriptive only; none can change the verdict)

- R1. Cost stress at 40 bps (already in P5).
- R2. No tradability filter, to show what the limit and suspension handling changes.
- R3. Drop the stale-membership window (2022-12-12 to 2023-03-17).
- R4. The 2023-2024 dev subperiod, the most recent two years of dev.

## 11. Trial accounting

The 30-test family above is the full set of planned trials. Any extra variant, lookback, filter, or
horizon tried later is logged in `EXPERIMENT_LOG.md` with its date and reason. It is then reported
with BH q-values computed over the enlarged family, next to the original. The primary hypothesis,
the splits, and the pass criteria do not change after dev results are seen. If a bug is found after an
OOS period is opened, the fix is logged and both the old and new numbers are reported.

## 12. Known limitations, accepted up front

- BaoStock membership is a weekly third-party snapshot, not CSIndex's official history. It has the
  14-week hole in section 2 and can lag a rebalance by up to a week.
- `float_cap` is a turnover-implied proxy, not CSI's free-float-adjusted cap.
- The Shenwan history comes from a current file of start dates. Later corrections may have been
  back-filled, which is a small, unmeasured look-ahead risk in neutralization.
- Daily bars only: no auction volume or intraday depth, so opening fills are assumed at the open
  print, and capacity is a rough estimate.
- Results from other vendors or formula variants of this factor are not directly comparable: data
  vendor, universe rule, and formula details all move the numbers.
