# Experiment log

Every trial, data incident, and protocol deviation is recorded here in date order, including the ones
that went badly. An entry is never edited after the fact; corrections are new entries. Run IDs refer
to directories under `runs/` (not published; each holds `summary.json` with the config, source, and
panel hashes).

## 2026-10-27 — E0: data-source probe (no research computation)

Purpose: choose public sources and check coverage before writing the protocol. No factor value,
forward return, or IC was computed.

- **BaoStock** (`baostock` 0.9.4): free, no registration; `bs.login()` uses the library's anonymous
  default. Its published access rules allow 50,000 requests a day and **no concurrent connections**.
- **Incident, recorded honestly.** Before reading those rules, a speed test opened 4 and then 8
  parallel BaoStock sessions for about one minute. Two things went wrong. Several parallel queries
  returned exactly 2,000 rows instead of 2,504, with no error, so parallel sessions can truncate a
  result silently. Then a killed test left four worker processes holding open connections for about
  30 minutes, until they were found and stopped. The IP was not blocked (login still succeeds). All
  later downloads are serial, and the downloader now fails on any page error instead of returning a
  short result.
- A second scratch bug: `while rs.next(): n += 1` loops forever, because BaoStock's `next()` advances
  only when `get_row_data()` is called. Fixed in the probe; the package always calls
  `get_row_data()`.
- **Speed.** About 15 s for one stock's 10-year daily history; about 40 s for one day of all
  A-shares through the bulk per-date endpoint (5,000+ rows). The per-stock route is used, limited to
  each code's membership window plus padding.
- **Membership.** Weekly snapshots from 2007; 500 names each. **Defect found:** the snapshot did not
  change from 2022-08-01 to 2023-03-20, so the 2022-12-12 rebalance is missing for 14 weeks.
- **AKShare** (`akshare` 1.20.1): Shenwan industry history (12,929 records, about 5 s), CSIndex
  H00905 total return and 000905 price index (about 2 s each), Sina IC futures per contract (served
  from contracts listed in late 2018; earlier contracts return an empty table). CFFEX's own daily
  endpoint took 76 s for one date and was not used. Eastmoney daily bars are fast (about 2 s a stock)
  but round turnover to 0.01 percentage points, too coarse for low-turnover names, so BaoStock is the
  bar source.

## 2026-10-27 — E1: protocol approved; environment

- Nick approved `docs/RESEARCH_PROTOCOL.md` as drafted: primary `RxT`/neutral/h = 20, P1-P5, a
  30-test BH family, dev 2018-2024, OOS-A 2025, OOS-B 2026-01 to 2026-09. No factor data had been
  computed at that point.
- Python environment outside iCloud Drive (iCloud hides dot-folders and corrupts in-tree
  environments, as measured in an earlier project). A shell hook sets `UV_PROJECT_ENVIRONMENT` only
  inside the project folder.
- **Deviation D-1 (wording only).** The public protocol words the author's earlier non-public work on
  this factor neutrally. The research content (formulas, splits, tests, thresholds) is unchanged from
  the approved text, which is kept outside the repository.

## 2026-10-27 — E2: independent code review before the first real run

An independent review of `src/` against this protocol ran before any factor result existed. Fixes,
none of which changes a research choice:

1. **P4 float error (high).** `scipy.stats.spearmanr` returns 0.8999999999999998 for one adjacent
   swap of five quintiles, so `>= 0.9` would have failed a result the protocol counts as monotone.
   `pipeline.monotonicity` now rounds to 10 decimals; a test pins the one-swap case at exactly 0.9.
2. **Long-short definition (medium).** The protocol defines Q5 - Q1 as the daily return difference of
   two drifting books; the first implementation differenced monthly period returns. It now
   simulates both books daily (`pipeline.long_short_daily`), with costs on each leg.
3. **Last-period survivorship (low).** The final holding of a split dropped a name with no bar on the
   label-end day; it now keeps the name's last close, as earlier periods already did. The run also
   counts labels that use a last close after a code's final bar (protocol section 6).
4. **ST price limits (low).** The ST limit was 5% on every board. Now: 5% for main-board ST until
   2026-07-06 (10% after, per the SSE/SZSE rule revision effective that day) and for ChiNext ST before
   2020-08-24; STAR ST stays 20%. ChiNext is matched by the `sz.30` prefix so 302xxx codes count.
5. **Benchmark gaps (low).** pandas 3 `pct_change` no longer fills gaps, so one missing index close
   would drop two days from every hedged series. Closes are carried forward and the gaps counted.
6. **`ADD` double winsorization (low).** The composite was winsorized again before its final
   z-score; it is now `z(z(w(REV20)) + z(w(LOWTURN20)))` as specified.

Not changed, documented instead: a held code whose bars end (delisting, or the end of its download
window after a long suspension) is closed at its last mark; and the first simulated day's return
runs open to close while the benchmark's runs close to close (one day per simulation).

## 2026-10-27 — E3: two more late membership windows; robustness check R3b added (before results)

Listing every date on which the weekly BaoStock snapshot changed (no factor computation) shows that
each semiannual rebalance appears on its effective date except three:

| Rebalance effective | First BaoStock snapshot with it | Lag |
|---|---|---|
| 2021-06-15 | 2021-07-19 | about 5 weeks |
| 2021-12-13 | 2022-01-17 | about 5 weeks |
| 2022-12-12 | 2023-03-20 | 14 weeks (known at protocol time) |

Section 12 of the protocol said the snapshot "can lag a rebalance by up to a week"; that was wrong
for these three cases. In each window about 50 of 500 names (10%) carry the old membership. Between
rebalances there are 10 small changes of one or two names (delistings, mergers; twice the list held
499 names for a few weeks). This source cannot show whether those were picked up on time.

Amendment, made before any result existed: the main run still uses the vendor snapshots as they are
(as the protocol says), R3 stays as pre-registered, and a new descriptive check **R3b** drops signal
dates in all three windows (`all_stale_membership_windows` in the config). Like every robustness
check, it cannot change the verdict.
- Futures coverage, also before results: Sina's per-contract IC history is truncated for early 2019
  contracts (IC1903 has 1 row, IC1904 has 2), and IC1901/IC1902/IC1812 are not served. The futures
  hedge therefore starts on **2019-04-22**, not 2019-01 as the protocol estimated. It stays a
  secondary hedge; the primary hedge (CSI 500 total return) covers the whole sample.

## 2026-10-27 19:41 — E4: first dev run, `dev-20261027-194156`

The first real run under the approved protocol (data: 1,216 codes, 1.32 M bars, panel hash in the
run's `summary.json`). Dev split only; no out-of-sample data was evaluated.

**Primary (`RxT`, neutral, h = 20): FAIL.**

| Criterion | Result | Pass |
|---|---|---|
| P1 mean Rank IC >= 0.02 | 0.0190 | no |
| P2 HAC t >= 2 and BH q <= 0.05 | t = 2.23, q = 0.027 | yes |
| P3 positive years >= 5 of 7 | 5 (2018-2022 positive; 2023 -0.0005, 2024 -0.0001) | yes |
| P4 monotonicity >= 0.9 and Q5 > Q1 | 0.1; Q5 - Q1 = +0.16% a month | no |
| P5 Q5 vs CSI 500 TR, 20 bps: excess > 0, IR >= 0.5; > 0 at 40 bps | -6.8% a year, IR -0.97 | no |

What the numbers say, before any further analysis: the daily IC is positive but small, and the
quintiles are an inverted U (monthly means Q1 -0.04%, Q2 0.63%, Q3 0.64%, Q4 0.49%, Q5 0.12%). Both
tails of `RxT` hold high-turnover names, and high turnover on its own predicts low returns
(`LOWTURN20` neutral: IC 0.066, HAC t 6.7, monotone quintiles). H3 is not supported:
`RxT - REV20` IC difference -0.0010, HAC t -0.45. H4 is not supported: `ADD` (IC 0.055) is below
`LOWTURN20` alone.

**Bug found while reading this run (reported numbers only, not the verdict).** In
`long_short_daily` the short leg was simulated as a long book with costs deducted, and subtracting it
added those costs back, so "net" long-short came out above "gross" (`LOWTURN20` neutral Sharpe
0.802 net vs 0.795 gross). Fixed: the short leg is now `-(gross) - (cost drag)`, with a test that
stress < net < gross. P1-P5 do not use the long-short numbers, so the verdict cannot change.

**Post-hoc diagnostics added (descriptive; they cannot change the verdict and add no signal):**
equal-weight eligible universe vs CSI 500 TR, gross and at 20 bps; the primary Q5 vs TR gross of
costs; and the IC futures basis drag (price-index return minus front-month return) on the dates the
futures hedge exists. They separate universe drift, costs, and basis from the signal itself.

The dev run is repeated after the fix as the run of record (E5). This run directory is kept.

## 2026-10-27 19:45 — E5: dev run of record, `dev-20261027-194525`

Same data and protocol as E4, with the long-short cost fix and the post-hoc diagnostics. Against
E4, 132 of 1,243 scalar fields changed, all of them long-short net/stress values, the new
diagnostics, the R2 long-short values, and the source hash; every IC, quintile mean, long-only
result, and the verdict are identical. `revturn reproduce` on this run (`dev-20261027-194649`)
matched 1,243 of 1,243 fields in 64 s.

Verdict unchanged: **primary FAIL** (P1 no, P2 yes, P3 yes, P4 no, P5 no).

Corrected long-short (Q5 - Q1, daily drifting books, annualized):

| Series (neutral) | Gross | Net 20 bps | Net 40 bps |
|---|---|---|---|
| `RxT` (primary) | +0.5%, Sharpe 0.10 | -6.3%, Sharpe -0.52 | -12.8% |
| `REV20` | -0.3% | -7.5% | -14.1% |
| `LOWTURN20` | +8.6%, Sharpe 0.80 | +4.8%, Sharpe 0.47 | +1.1% |
| `ADD` | +4.5% | -1.7% | -7.5% |

Post-hoc diagnostics: the equal-weight eligible universe beat CSI 500 TR by 1.4% a year gross
(1.1% at 20 bps), so universe drift does not explain the primary's loss. The primary Q5 trailed TR
by 3.5% a year gross and 6.8% at 20 bps: about 3.3 points are costs at 146% monthly two-way turnover,
and the rest is the signal's top bucket. The IC futures hedge cost 7.1% a year in basis over
2019-04-22 to 2024-12-31 relative to the price index.

Robustness (descriptive): R2 no tradability filter IC 0.0192; R3 drop the Dec-2022 stale window IC
0.0194; R3b drop all three stale windows IC 0.0210 (t 2.48); R4 2023-2024 IC -0.0003 and Q5 vs TR
-10.6% a year. None of these can change the verdict.

`LOWTURN20` (neutral) is the strongest series in the family, but it was a component, not the
primary. Promoting it now would be selection after seeing results. If it is pursued, it needs its
own pre-registered test; the 2025 and 2026 holdouts are not used to choose it.

Next step per protocol: write the freeze, then open OOS-A once and OOS-B once.

## 2026-10-27 — E6: post-dev confirmatory test registered (before any OOS data was opened)

Nick approved registering `LOWTURN20` (neutral, h = 20) as a **post-dev confirmatory test**, written
here before the freeze and before OOS-A or OOS-B was opened. It is labeled as chosen after seeing dev
results: it was the strongest of the ten series in dev (IC 0.066, HAC t 6.7), so its dev numbers are
selected and therefore optimistic. Only the holdouts can test it fairly.

- Criteria: the protocol's OOS criteria, unchanged: mean Rank IC (h = 20) > 0, and the top quintile
  hedged with CSI 500 TR has excess return > 0 net of 20 bps. Judged on OOS-A and OOS-B separately.
- The primary stays `RxT` (neutral, h = 20) and its dev verdict (FAIL) stands. The OOS verdicts of
  both are reported side by side. No other series is added.
- Config key `confirmatory_secondary`; summary field `confirmatory_secondary`.

## 2026-10-27 20:14 — E7: freeze

Dev run for the freeze: `dev-20261027-201353` (identical to the run of record E5 apart from the E6
fields and hashes: 22 of 1,264 fields, all new or hash fields). `configs/freeze.json` records the
SHA-256 of `configs/default.json` and of `src/revturn/`. From here, an out-of-sample run refuses to
start if either changes. Nick approved opening OOS-A and then OOS-B, once each.

## 2026-10-27 20:14 — E8: OOS-A opened once, `oos_a-20261027-201427`

Signals 2025-01-02 to 2025-12-31, 11 rebalances (the first trades on 2025-02-05, after the Spring
Festival), labels end by 2025-12-31. Freeze check passed.

| Test | Mean Rank IC (h = 20) | HAC t | Q5 vs CSI 500 TR, net 20 bps | Verdict |
|---|---|---|---|---|
| Primary `RxT` neutral | 0.0513 | 3.16 | +0.4% a year (IR 0.10); -3.0% at 40 bps | **PASS** |
| Confirmatory `LOWTURN20` neutral | 0.0251 | 0.82 | -7.0% a year (IR -1.26) | **FAIL** |

The primary passes only the protocol's deliberately weak OOS rule (signs). Its quintiles are a U
(monthly Q1 3.04%, Q2 1.94%, Q3 2.07%, Q4 2.54%, Q5 3.28%; monotonicity 0.4) and it loses money at
40 bps. The low-turnover premium that was strongest in dev reversed in 2025 (quintile monotonicity
-0.7). Context: CSI 500 TR +32.5% in 2025; the equal-weight eligible universe trailed TR by 4.1% a
year gross; IC futures basis drag 13.3% a year (2025-02-05 to 2025-12-31).

## 2026-10-27 20:15 — E9: OOS-B opened once, `oos_b-20261027-201515`

Signals 2026-01-05 to 2026-09-30, 9 rebalances (first trade 2026-02-02), labels end by 2026-10-26.
This is the only period no version of this study had seen.

| Test | Mean Rank IC (h = 20) | HAC t | Q5 vs CSI 500 TR, net 20 bps | Verdict |
|---|---|---|---|---|
| Primary `RxT` neutral | 0.0207 | 0.63 | +21.0% a year (IR 1.43); +16.6% at 40 bps | **PASS** |
| Confirmatory `LOWTURN20` neutral | 0.0383 | 1.39 | +0.3% a year (IR 0.09) | **PASS** |

Both pass the sign rule, but neither IC is significant, and the window is 9 months. The market fell
(CSI 500 TR -9.0% from 2025-12-31 to 2026-10-26, with -12% in March and -17% in July). In that market
the primary's quintiles were monotone (Q1 -3.06% to Q5 -0.18% a month): high-turnover losers fell
least. `RxT` was significantly worse than plain `REV20` here (IC difference -0.015, HAC t -4.2), so
the turnover interaction again added nothing. The data were checked before accepting this result:
BaoStock and CSIndex agree on the index level (6,712.73 on 2026-10-26), and member returns are
consistent with the index path.

## 2026-10-27 — Summary of the study (no further trials planned)

- The pre-registered primary failed its development screen (P1, P4, P5). Out of sample it met the
  weak sign rule in both holdouts, with an IC that was significant in 2025 only, and a hedged result
  that was about zero in 2025 and strongly positive in a falling 2026.
- The interaction hypothesis H3 was not supported in any period: `RxT` never beat plain `REV20`.
- The low-turnover component was the best dev series but failed its confirmatory test in 2025 and
  barely passed in 2026, which is what selection after seeing results usually looks like.
- Costs (about 150% monthly two-way turnover) and the IC futures basis (7% to 13% a year) decide the
  implementable result more than the signal does.
- Trial count: 30 pre-registered tests per split and one post-dev confirmatory test. Dev was run
  four times (E4 first; E5 run of record after a reporting-only fix; its reproduction; the E7 freeze
  copy), all with identical ICs and verdict. OOS-A and OOS-B were each run once. No variant was added.

## 2026-10-27 — E10: wording-only edits for publication

At Nick's request before publishing: the README gained a provenance section (an independent
public-data reproduction of a research direction from a 2025 internship; no internal data, code, or
results), and an MIT licence was added. Entry E1 was edited in place to replace machine-specific
shell-configuration details with a generic sentence; this is the only edit to an earlier entry, and
it changes no research content. No run, number, or verdict changed.
