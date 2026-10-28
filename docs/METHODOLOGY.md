# Methodology

How the pipeline turns public vendor files into the numbers in the README, and where each
assumption lives in code. The research choices themselves are fixed in
[`RESEARCH_PROTOCOL.md`](RESEARCH_PROTOCOL.md); this file explains how they are implemented.

## 1. Pipeline

```
revturn download   BaoStock (bars, membership, calendar, listing dates)  -> data/raw/baostock/
                   AKShare (Shenwan history, CSIndex indexes, IC futures) -> data/raw/akshare/
revturn build      raw strings -> typed, calendar-aligned panel        -> data/processed/
revturn run        panel -> signals -> labels -> IC family, quintiles,
                   long-only simulation, pass/fail                     -> runs/<split>-<time>/
revturn freeze     hash config + src/ after the dev run                -> configs/freeze.json
revturn reproduce  rerun a split and compare every scalar in summary.json
```

| Stage | Module | Main functions |
|---|---|---|
| Download | `download.py` | `download_baostock`, `download_akshare`, `manifest` |
| Panel | `panel.py` | `adjusted_index`, `limit_pct`, `limit_flags`, `asof_membership`, `asof_industry`, `float_cap`, `front_month_returns` |
| Universe | `universe.py` | `eligible`, `entry_tradable`, `buyable_at_open`, `sellable_at_open` |
| Signals | `factors.py` | `inputs`, `raw_signals`, `winsorize_mad`, `zscore`, `neutralize`, `processed_signals` |
| Labels | `labels.py` | `forward_returns`, `exit_prices`, `label_end_ok` |
| Statistics | `stats.py` | `rank_ic`, `newey_west_se`, `hac_test`, `bh_qvalues`, `ic_summary`, `per_year` |
| Portfolios | `portfolio.py` | `schedule`, `quintile_study`, `simulate_long`, `perf` |
| Orchestration | `pipeline.py` | `run`, `ic_family`, `long_only`, `evaluate_pass` |
| Guard | `freeze.py` | `write_freeze`, `check_freeze` |

Every frame in the panel is wide: rows are exchange trading days, columns are BaoStock codes. A code
without a bar on a day is `NaN`. Cross-sectional operations are row operations, so a value at row t
can only use rows at or before t unless a function says otherwise.

## 2. Timing and the look-ahead boundary

```
          close t                      open t+1                      open t+1+h
  ---------|------------------------------|------------------------------|------->
  signal computed from bars <= t   trade (entry price)            exit price for label h
  membership: snapshot dated <= t   tradability checked here
  industry: Shenwan record <= t     (suspended / limit open)
```

- **Signals** use `adj_close`, `turn`, `tradestatus`, `member`, `is_st`, `listed_days`, `industry`,
  and `float_cap` up to row t. `tests/test_lookahead.py` checks prefix invariance (computing on data
  truncated at t gives identical values at t), and that scrambling every input after t leaves every
  signal and the eligibility mask at or before t unchanged.
- **Labels** start at the t+1 open. Changing the close of t changes the signal at t but not the
  label at t (tested). The close-t to open-t+1 move is never in a label.
- **`entry_tradable`** is the one forward-shifted mask. It reads the t+1 session's status and opening
  price, because that is when the trade happens. It is used only to filter evaluation, never as a
  signal input.
- **Split boundaries.** `label_end_ok` drops signal dates whose label would end after the split's
  label end. The portfolio schedule only trades opens on or before the label end and marks the last
  holding at that day's close. Scrambling prices after the label end leaves the quintile table and the
  simulated daily returns unchanged (tested).
- **Out-of-sample access.** `pipeline.run` calls `check_freeze` for OOS splits. Without
  `configs/freeze.json`, or with a config or `src/` hash that differs from it, the run stops (tested).

## 3. Prices and adjustment

BaoStock bars are downloaded unadjusted (`adjustflag=3`). `preclose` is the exchange's ex-rights
reference price, so the daily ratio `close_t / preclose_t` is the total return of a holder who keeps
dividends and bonus shares in the stock:

```
A_t = prod_{s <= t} close_s / preclose_s        adj_open_t = A_t * open_t / close_t
```

This is BaoStock's own "percent-change" back-adjustment up to a constant per code. A sample of 22
codes downloaded with `adjustflag=1` checks it; the result is in `DATA_PROFILE.md`. Suspended days have
OHLC equal to the previous close, so they contribute a ratio of 1. Limit prices use unadjusted
`preclose`, rounded half-up to the cent, because the exchange computes them that way. Limits: main
board 10%; STAR 20%; ChiNext (`sz.30*`) 20% from 2020-08-24 and 10% before; ST names 5% on the main
board until 2026-07-06 (10% after) and on ChiNext before 2020-08-24, while STAR ST stays 20%.

## 4. Universe and tradability

`eligible` (row t) = CSI 500 member by the latest weekly snapshot dated on or before t, not ST,
traded on t, at least 60 sessions since listing, and traded on at least 15 of the last 20 sessions.

At the t+1 open:

| Situation | IC and quintile research | Long-only simulation |
|---|---|---|
| Suspended | excluded | cannot buy, cannot sell (sell retried next open) |
| Opens at limit-up | excluded | cannot buy; can sell |
| Opens at limit-down | excluded | can buy; cannot sell (retried) |

The research filter is symmetric because IC and quintiles are two-sided. The simulator is
asymmetric because a real long book only needs the side it trades.

## 5. Signals

`r20 = A_t / A_{t-20} - 1` uses 20 exchange sessions, so a suspension inside the window counts as a
zero-return day. `turn20` averages `turn` over traded days only, with at least 15 required.

| ID | Formula (higher = buy) |
|---|---|
| `REV20` | `-r20` |
| `LOWTURN20` | `-turn20` |
| `RxT` | `-(r20 - median_t r20) * turn20` |
| `RxT_RANK` | `(rank_t(-r20) - 0.5) * rank_t(turn20)` |
| `ADD` | `z(z(w(REV20)) + z(w(LOWTURN20)))` |

The median in `RxT` matters. On a day when the whole market falls 10%, every raw `-r20` is positive,
and a plain product would rank stocks by turnover alone. Demeaning first keeps `RxT` a relative
reversal bet that turnover scales.

Processing per day over eligible names: `winsorize_mad` (median +/- 5 MAD), `zscore`, then for the
neutral version `neutralize`: OLS of the z-score on Shenwan level-1 dummies plus z(log float cap), with
industries under 5 names pooled. The residual is z-scored again. The neutral version removes linear
industry and size exposure from the ranking. It does not remove beta or style exposures beyond size.

## 6. Statistics

- `rank_ic` ranks signal and label on the common set of names each day and correlates the ranks. It
  is vectorized over the whole matrix and checked against `scipy.stats.spearmanr` with NaNs and
  ties.
- `newey_west_se` uses Bartlett weights `1 - j / (L + 1)`. The lag is `L = max(5, 2h)`: an h-day label
  overlaps its neighbours for h - 1 days, so daily ICs are autocorrelated by construction, and
  doubling the lag is conservative. A test shows HAC widens the standard error of an MA(20) series
  as expected.
- `bh_qvalues` is the Benjamini-Hochberg step-up procedure over all 30 tests in the family.
- ICIR is the mean of daily IC divided by its standard deviation, not annualized. For overlapping
  20-day labels an annualized ICIR would overstate the number of independent observations.

## 7. Portfolios

- **Schedule.** Signals on the last session of each month; trades at the next open.
- **Quintiles.** Equal weight at entry, buy-and-hold to the next trade open. Two-way turnover per
  bucket is the sum of absolute differences between new target weights and the drifted old weights.
  The first rebalance counts the full initial buy (turnover 1.0).
- **Long-short.** Two drifting equal-weight books, Q5 and Q1, formed on names tradable at the next
  open and simulated daily with costs on each leg; the long-short return is the daily difference
  (`pipeline.long_short_daily`). A diagnostic only. The monthly bucket means used for monotonicity
  (P4) come from `quintile_study`.
- **Long-only simulator.** Positions are marked open-to-close and close-to-open with adjusted
  prices. On a trade day: sells first (blocked ones stay and are retried at every later open), then
  equal-weight targets over names that are held or buyable. Costs are `c * |traded value|`, deducted
  from cash. Cash earns zero. A held code with no further bars (delisted, or past the end of its
  download window after a long suspension) is closed at its last mark. The first simulated day
  runs open to close while the benchmark runs close to close; this affects one day per run.
- **Hedges.** Daily `r_long - r_benchmark` (index closes carried forward over any vendor gap, and
  the gaps counted) for the CSI 500 total return index, the price index, and
  the front-month IC future. The IC series holds the expiring contract through its last session and
  measures the next day on the new front month; contracts before late 2018 are not available, so
  that hedge starts 2019-01.
- **Annualization.** 242 sessions a year for daily series, 12 for monthly. Sharpe and IR use a zero
  risk-free rate. Maximum drawdown includes the starting NAV of 1.

## 8. Post-dev additions (logged before the holdouts were opened)

- **Confirmatory secondary test** (`confirmatory_secondary` in the config, log entry E6):
  `LOWTURN20`, neutral, h = 20, judged on each holdout by the protocol's OOS rule only. It is labeled
  as chosen after the dev results; its dev numbers are selected and therefore optimistic.
- **Post-hoc diagnostics** (`post_hoc_diagnostics` in `summary.json`, log entry E4): the equal-weight
  eligible universe against CSI 500 TR, the primary top quintile gross of costs, and the IC futures
  basis drag on the futures dates. They add no signal and cannot change a verdict.
- **R3b**: drops all three late-membership windows (log entry E3).

## 9. Reproducibility

`summary.json` stores the config, the SHA-256 of the config, of `src/`, and of the processed panel,
and every scalar result. `revturn reproduce --run runs/<id>` reruns that split and compares every
scalar field except `run_id`, `created_at`, and `note`. Run directories are never overwritten.
