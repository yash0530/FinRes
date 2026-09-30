# FinRes validation lab — report

Method: pre-registered in [ADR-004 / ADR-004a](../DECISIONS.md) — 36 configs ({W1,W2,W3} × {G1,G0} × {S1,S2,S3} × {Nvar,N3}), IS 2013-01→2018-12, OOS 2019-01→2026-08, $2,500/month, signals at month-end close, fills at next-day close, 15 bps per side, cash earns 0.

## Data
- S&P 500 point-in-time membership: `lab/sp500_history.csv` from https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv (fetched 2026-09-27).
- ai: 145 tickers, 145 with prices, 140 with EDGAR facts.
- sp500: 815 tickers, 635 with prices, 618 with EDGAR facts.
- S&P members (as of mid-year) without Yahoo prices: {'2013': 120, '2014': 118, '2015': 109, '2016': 98, '2017': 87, '2018': 83, '2019': 72, '2020': 55, '2021': 47, '2022': 34, '2023': 24, '2024': 16, '2025': 12, '2026': 0}

## Sanity checks
- Random-signal ranker percentile: 47.1 (expect 35–65): pass
- EWU first 3 months match hand recomputation: pass
- Lookahead tests: pass

## In-sample (2013-01→2018-12), sorted by XIRR
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G0-S3-N3 | 27.3% | 24.9% | 20.3% | -24.1% | 1.20 | 0.25 | 0.71 |
| W2-G1-S2-Nvar | 24.3% | 24.6% | 20.3% | -30.5% | 1.19 | 0.15 | 0.84 |
| W2-G0-S3-Nvar | 23.7% | 24.5% | 18.8% | -21.6% | 1.27 | 0.22 | 0.77 |
| W1-G0-S3-Nvar | 23.5% | 23.1% | 18.0% | -21.0% | 1.25 | 0.27 | 0.56 |
| W1-G1-S3-Nvar | 23.5% | 23.2% | 18.1% | -21.4% | 1.25 | 0.26 | 0.56 |
| W2-G1-S3-Nvar | 23.3% | 24.2% | 18.0% | -21.6% | 1.30 | 0.21 | 0.72 |
| W2-G1-S3-N3 | 22.6% | 21.6% | 19.2% | -24.8% | 1.12 | 0.23 | 0.32 |
| W3-G0-S3-Nvar | 22.0% | 22.1% | 17.8% | -18.3% | 1.22 | 0.28 | 0.50 |
| W3-G0-S3-N3 | 21.8% | 22.4% | 17.9% | -17.0% | 1.23 | 0.28 | 0.64 |
| W1-G0-S3-N3 | 21.0% | 19.3% | 17.9% | -21.7% | 1.08 | 0.28 | 0.04 |
| W2-G1-S2-N3 | 20.9% | 19.7% | 18.5% | -22.0% | 1.07 | 0.15 | 0.11 |
| W3-G0-S2-N3 | 20.8% | 21.9% | 17.5% | -23.5% | 1.23 | 0.15 | 0.56 |
| W3-G0-S2-Nvar | 20.5% | 21.2% | 16.3% | -22.9% | 1.27 | 0.14 | 0.37 |
| W2-G0-S2-Nvar | 19.8% | 21.0% | 18.1% | -25.7% | 1.15 | 0.16 | 0.33 |
| W1-G1-S3-N3 | 19.2% | 17.4% | 17.9% | -21.2% | 0.99 | 0.29 | -0.19 |
| W1-G0-S2-Nvar | 19.1% | 20.1% | 16.7% | -24.0% | 1.19 | 0.15 | 0.14 |
| W1-G1-S2-N3 | 19.0% | 18.6% | 18.6% | -23.3% | 1.01 | 0.16 | -0.02 |
| W1-G0-S2-N3 | 18.8% | 18.5% | 17.3% | -21.6% | 1.08 | 0.16 | -0.07 |
| W3-G1-S3-N3 | 18.7% | 20.0% | 17.6% | -17.2% | 1.13 | 0.28 | 0.18 |
| W1-G1-S2-Nvar | 18.7% | 19.5% | 17.1% | -23.4% | 1.13 | 0.15 | 0.05 |
| W2-G0-S2-N3 | 18.6% | 17.9% | 17.9% | -23.7% | 1.01 | 0.16 | -0.14 |
| W3-G1-S2-Nvar | 18.0% | 19.1% | 16.3% | -22.9% | 1.16 | 0.15 | -0.04 |
| W3-G1-S2-N3 | 17.4% | 19.1% | 16.4% | -20.7% | 1.15 | 0.15 | -0.03 |
| W3-G1-S3-Nvar | 17.2% | 17.7% | 16.7% | -22.4% | 1.06 | 0.30 | -0.27 |
| W1-G0-S1-Nvar | 14.5% | 16.3% | 14.8% | -19.8% | 1.10 | 0.06 | -0.62 |
| W2-G0-S1-N3 | 14.5% | 14.9% | 16.2% | -19.3% | 0.94 | 0.06 | -0.66 |
| W1-G1-S1-Nvar | 14.2% | 16.0% | 14.8% | -19.6% | 1.09 | 0.06 | -0.67 |
| W2-G1-S1-N3 | 13.9% | 14.6% | 16.2% | -19.2% | 0.93 | 0.06 | -0.72 |
| W3-G0-S1-Nvar | 13.8% | 15.8% | 15.0% | -20.5% | 1.06 | 0.06 | -0.85 |
| W2-G0-S1-Nvar | 13.8% | 16.2% | 15.2% | -17.7% | 1.06 | 0.06 | -0.59 |
| W3-G1-S1-Nvar | 13.8% | 15.8% | 15.0% | -20.5% | 1.06 | 0.06 | -0.86 |
| W2-G1-S1-Nvar | 13.5% | 15.9% | 15.2% | -17.9% | 1.05 | 0.06 | -0.64 |
| W1-G1-S1-N3 | 13.5% | 14.6% | 15.5% | -19.5% | 0.96 | 0.06 | -0.88 |
| W1-G0-S1-N3 | 13.5% | 14.6% | 15.5% | -19.5% | 0.96 | 0.06 | -0.88 |
| W3-G1-S1-N3 | 13.2% | 15.5% | 15.7% | -20.7% | 1.00 | 0.06 | -0.98 |
| W3-G0-S1-N3 | 13.2% | 15.5% | 15.7% | -20.7% | 1.00 | 0.06 | -0.98 |
| EWU | 13.7% | 18.3% | 20.6% | -26.1% | 0.93 | 0.06 | nan |
| B0 | 17.3% | 19.8% | 16.5% | -20.5% | 1.19 | 0.16 | 0.14 |

**Selected:** W2-G0-S3-N3 (highest IS XIRR with maxDD not >10 pp worse than EWU's).

## Out-of-sample (2019-01→2026-08)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G0-S3-N3 | 41.6% | 35.5% | 38.7% | -35.4% | 0.97 | 0.26 | 0.24 |
| B0 | 39.8% | 39.4% | 38.4% | -31.0% | 1.06 | 0.15 | 0.80 |
| EWU | 36.9% | 35.5% | 32.5% | -32.6% | 1.10 | 0.05 | nan |
| DCA SPY | 16.7% | 16.4% | 17.1% | -23.8% | 0.98 | 0.00 | -2.51 |
| DCA QQQ | 20.6% | 21.7% | 21.7% | -33.7% | 1.02 | 0.00 | -1.97 |
| DCA SMH | 39.9% | 38.4% | 33.0% | -39.6% | 1.16 | 0.00 | 0.43 |

## Random portfolios (1,000 each)
| Variant | Selected percentile | Median | 60th (threshold) | 75th |
|---|---|---|---|---|
| Same pool (seed 11) | 67.5 | 36.8% | **39.4%** | 44.6% |
| All eligible, no gate (context, seed 13) | 64.8 | 37.7% | 40.3% | 44.8% |

## S&P 500 co-gate (point-in-time members, 2013-01→2026-08)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G0-S3-N3 | 8.6% | 7.4% | 18.4% | -26.7% | 0.48 | 0.20 | -2.27 |
| EWU | 15.3% | 15.0% | 15.7% | -28.2% | 0.97 | 0.03 | nan |

## Decision
- beats_b0: yes
- ge_p60_random: yes
- sp500_cogate: no
- **Ship: B0**

## Eligible names per year (min / median / max)
| Year | AI | S&P 500 |
|---|---|---|
| 2013 | [88, 89, 92] | [346, 349, 351] |
| 2014 | [92, 92, 94] | [351, 355, 359] |
| 2015 | [94, 95, 96] | [360, 367, 372] |
| 2016 | [96, 96, 98] | [374, 385, 393] |
| 2017 | [98, 98, 100] | [394, 402, 406] |
| 2018 | [101, 101, 103] | [407, 410, 419] |
| 2019 | [103, 105, 107] | [419, 421, 431] |
| 2020 | [107, 107, 111] | [432, 441, 443] |
| 2021 | [111, 112, 117] | [445, 450, 454] |
| 2022 | [119, 124, 127] | [455, 464, 471] |
| 2023 | [127, 132, 132] | [470, 475, 477] |
| 2024 | [132, 132, 134] | [478, 481, 485] |
| 2025 | [134, 138, 139] | [485, 489, 493] |
| 2026 | [139, 141, 141] | [494, 496, 500] |

## Implementation fidelity (post-hoc, not a selection)
B0 buys every uptrend name each month (~60–130 orders). The app ships the practical version `ew_trend10`: 10 uptrend names/month, least-held first (≤3 per group), S2 sells. It should track B0.
| Universe · period | B0 (ew_trend) | Practical (ew_trend10) | EWU |
|---|---|---|---|
| ai · IS | 17.3% (dd -20%) | 20.8% (dd -20%) | 13.7% (dd -26%) |
| ai · OOS | 39.8% (dd -31%) | 40.0% (dd -34%) | 36.9% (dd -33%) |
| ai · FULL | 32.1% (dd -35%) | 32.9% (dd -36%) | 29.1% (dd -34%) |
| sp500 · FULL | 11.3% (dd -25%) | 12.5% (dd -24%) | 15.3% (dd -28%) |

## Year by year, AI universe (time-weighted return; context only)
The shipped rule beat the equal-weight universe in 10 of 14 calendar years.
| Year | rule (ew_trend10) | EWU | DCA SMH |
|---|---|---|---|
| 2013 | +32.8% | +43.4% | +22.0% |
| 2014 | +25.0% | +17.1% | +31.3% |
| 2015 | +3.0% | -7.2% | -1.1% |
| 2016 | +44.6% | +36.2% | +37.2% |
| 2017 | +43.8% | +37.7% | +41.9% |
| 2018 | -2.0% | -7.3% | -10.7% |
| 2019 | +44.9% | +49.7% | +67.0% |
| 2020 | +100.2% | +69.4% | +52.3% |
| 2021 | +22.6% | +36.4% | +45.0% |
| 2022 | -25.4% | -32.8% | -35.5% |
| 2023 | +65.2% | +62.6% | +68.8% |
| 2024 | +65.7% | +52.0% | +45.5% |
| 2025 | +48.1% | +45.0% | +53.0% |
| 2026 | +12.4% | +24.9% | +46.1% |

## Hindsight-free re-test (ADR-007)
H1, one run, no selection, 2010-01→2026-08: the shipped rule (B0R + S2, `ew_trend10`) on a universe rebuilt each year from the 10-Ks filed the year before (SIC + frozen dictionary), with PIT revenue ≥ $100M. Sanity on the PIT universe: random-signal percentile 54.2, EW first 3 months pass; repeated from 2014-01 (first month with ≥ 10 eligible names): percentile 39.3, EW first 3 months pass.

### PIT universe, priced names only
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| rule (ew_trend10) | 26.6% | 18.8% | 32.8% | -56.6% | 0.68 | 0.25 | -2.12 |
| EW (ew_all) | 44.2% | 34.8% | 39.5% | -55.5% | 0.95 | 0.02 | nan |
| DCA SMH | 29.7% | 26.7% | 26.7% | -39.6% | 1.03 | 0.00 | -1.37 |
| DCA QQQ | 19.5% | 19.4% | 18.5% | -33.7% | 1.06 | 0.00 | -2.16 |

### STRESS TEST: PIT + 110 unpriced names as EW-index clones ending in a −55% (Nasdaq) / −30% day
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| rule (ew_trend10) | 25.6% | 18.7% | 32.5% | -54.8% | 0.68 | 0.21 | -2.00 |
| EW (ew_all) | 41.2% | 32.5% | 37.9% | -53.9% | 0.93 | 0.02 | nan |
| DCA SMH | 29.7% | 26.7% | 26.7% | -39.6% | 1.03 | 0.00 | -1.11 |
| DCA QQQ | 19.5% | 19.4% | 18.5% | -33.7% | 1.06 | 0.00 | -1.99 |

### 2026 hand-picked AI universe, same period (bias estimate)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| rule (ew_trend10) | 28.2% | 25.4% | 27.8% | -36.1% | 0.95 | 0.14 | 0.78 |
| EW (ew_all) | 26.6% | 23.6% | 25.0% | -34.3% | 0.98 | 0.02 | nan |

Stress clones die on post-t dates (Tiingo end date, else last 10-K + 365 days), known only after the fact: a stress assumption about how the unpriced names ended, not a signal the rule could trade.

### Headline (XIRR differences)
- rule_minus_smh_priced: -3.1%
- rule_minus_smh_stressed: -4.1%
- rule_minus_ew_pit: -17.6%
- bias_hand_minus_pit: +1.6%
- **The rule's XIRR on the PIT universe was BELOW monthly SMH DCA. Per ADR-007 the app says so plainly and Yash decides; there is no automatic switch.**

### Year by year, PIT priced (time-weighted)
| Year | rule (ew_trend10) | EW (ew_all) | DCA SMH | DCA QQQ |
|---|---|---|---|---|
| 2010 | -30.4% | -11.9% | +29.2% | +29.0% |
| 2011 | -11.3% | -9.8% | -5.6% | +3.8% |
| 2012 | -5.6% | -8.4% | +11.3% | +19.6% |
| 2013 | +25.9% | +32.9% | +26.9% | +31.4% |
| 2014 | +12.6% | +15.9% | +31.3% | +19.8% |
| 2015 | -7.5% | +9.0% | -1.1% | +7.4% |
| 2016 | +72.6% | +122.7% | +37.2% | +10.4% |
| 2017 | +50.8% | +65.2% | +41.9% | +33.8% |
| 2018 | +2.2% | -17.6% | -10.7% | -1.5% |
| 2019 | +54.7% | +69.7% | +67.0% | +40.7% |
| 2020 | +84.4% | +90.7% | +52.3% | +43.9% |
| 2021 | +24.4% | +99.0% | +45.0% | +30.5% |
| 2022 | -25.5% | -49.6% | -35.5% | -33.7% |
| 2023 | +36.7% | +187.6% | +68.8% | +53.3% |
| 2024 | +76.0% | +151.3% | +45.5% | +27.5% |
| 2025 | -15.1% | +36.5% | +53.0% | +20.8% |
| 2026 | +59.0% | +20.0% | +46.1% | +15.7% |

### Eligible names per month-end (min / median / max; < 40 = unscored)
| Year | PIT priced | PIT stress | hand-picked |
|---|---|---|---|
| 2010 | [0, 1, 2] | [0, 1, 2] | [81, 81, 82] |
| 2011 | [2, 4, 4] | [3, 8, 9] | [83, 84, 87] |
| 2012 | [5, 6, 8] | [11, 17, 21] | [87, 87, 88] |
| 2013 | [7, 7, 7] | [19, 21, 21] | [88, 89, 92] |
| 2014 | [13, 13, 13] | [28, 29, 29] | [92, 92, 94] |
| 2015 | [13, 14, 14] | [32, 33, 34] | [94, 95, 96] |
| 2016 | [24, 25, 25] | [40, 42, 42] | [96, 96, 98] |
| 2017 | [22, 22, 22] | [38, 38, 40] | [98, 98, 100] |
| 2018 | [16, 17, 17] | [34, 36, 39] | [101, 101, 103] |
| 2019 | [20, 24, 24] | [40, 44, 46] | [103, 105, 107] |
| 2020 | [28, 28, 29] | [47, 47, 49] | [107, 107, 111] |
| 2021 | [30, 31, 31] | [46, 49, 51] | [111, 112, 117] |
| 2022 | [36, 37, 37] | [44, 47, 50] | [119, 124, 127] |
| 2023 | [44, 44, 45] | [51, 53, 54] | [127, 132, 132] |
| 2024 | [51, 52, 53] | [56, 57, 57] | [132, 132, 134] |
| 2025 | [76, 78, 78] | [84, 84, 85] | [134, 138, 139] |
| 2026 | [96, 96, 97] | [99, 100, 101] | [139, 141, 141] |

### Coverage
`priced` = prices as `C{cik}` in lab/data/lab.db inside the year; unpriced company-years enter only the delisting stress test. Duplicate CIKs on one ticker are dropped. The $100M floor is applied per month.

| year | eligible | mapped | listed | delisted | unmapped | dup dropped | priced | unpriced | priced share |
|---|---|---|---|---|---|---|---|---|---|
| 2010 | 15 | 10 | 4 | 6 | 5 | 0 | 4 | 11 | 27% |
| 2011 | 24 | 19 | 7 | 12 | 5 | 0 | 6 | 18 | 25% |
| 2012 | 30 | 25 | 9 | 16 | 5 | 0 | 9 | 21 | 30% |
| 2013 | 33 | 28 | 8 | 20 | 5 | 0 | 8 | 25 | 24% |
| 2014 | 42 | 38 | 13 | 25 | 4 | 0 | 13 | 29 | 31% |
| 2015 | 54 | 48 | 16 | 32 | 6 | 0 | 15 | 39 | 28% |
| 2016 | 58 | 55 | 28 | 27 | 3 | 0 | 28 | 30 | 48% |
| 2017 | 57 | 51 | 26 | 25 | 6 | 0 | 26 | 31 | 46% |
| 2018 | 51 | 44 | 19 | 25 | 7 | 0 | 19 | 32 | 37% |
| 2019 | 57 | 52 | 27 | 25 | 5 | 0 | 27 | 30 | 47% |
| 2020 | 66 | 61 | 35 | 26 | 5 | 0 | 35 | 31 | 53% |
| 2021 | 64 | 60 | 38 | 22 | 4 | 0 | 38 | 26 | 59% |
| 2022 | 65 | 63 | 43 | 20 | 2 | 0 | 43 | 22 | 66% |
| 2023 | 71 | 66 | 54 | 12 | 5 | 0 | 54 | 17 | 76% |
| 2024 | 78 | 70 | 63 | 7 | 8 | 0 | 63 | 15 | 81% |
| 2025 | 103 | 96 | 88 | 8 | 7 | 0 | 88 | 15 | 85% |
| 2026 | 129 | 117 | 111 | 6 | 12 | 0 | 111 | 18 | 86% |
| all (after dups) | 997 | | | | | | 587 | 410 | 59% |

**Honesty (ADR-007).** The rule was chosen on data that overlaps 2019–2026. The PIT universe is a new *universe*, not new *time*. Vocabulary drifts ("big data" era, 2010–13), so the eligible counts for each year are reported, and years with fewer than 40 eligible names are unscored. The dictionary was written in 2026, so some hindsight remains in the choice of words. That is why it is frozen here before scoring, with a generic-tech robustness run.

## H2 variants (ADR-007)
V1 Faber monthly (month-end close vs 10-month SMA), V2 buffer (enter > SMA200 × 1.02 with SMA50 > SMA200, exit 2 month-ends < SMA200 × 0.98), V3 FIP (uptrend → top third by 12-2 momentum → lower half by ID). All use B0R's least-held rotation (10 names, ≤ 3/group) and the −35% stop. PIT universe, priced names; months with < 40 eligible names are unscored (equal weight into all).

### In-sample 2010-01→2017-12
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| V1 | 22.5% | 7.6% | 32.6% | -54.2% | 0.38 | 0.33 | -3.23 |
| V2 | 21.9% | 7.7% | 32.1% | -52.8% | 0.38 | 0.29 | -2.76 |
| V3 | 23.1% | 9.1% | 36.2% | -56.6% | 0.40 | 0.32 | -2.09 |
| B0R | 23.1% | 9.1% | 36.2% | -56.6% | 0.40 | 0.32 | -2.09 |
| EW (ew_all) | 36.2% | 20.9% | 33.9% | -49.8% | 0.72 | 0.04 | nan |

Unscored months (< 40 eligible: buys = all eligible names, so only the exits differ): IS V1 96/96, V2 96/96, V3 96/96, B0R 96/96, EW (ew_all) 96/96; OOS V3 60/104, B0R 60/104, EW (ew_all) 60/104.

**Best IS variant: V3.** Out-of-sample 2018-01→2026-08 (run once):
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| V3 | 31.6% | 30.5% | 29.5% | -33.7% | 1.05 | 0.22 | 0.66 |
| B0R | 28.6% | 28.3% | 28.8% | -33.7% | 1.01 | 0.23 | 0.20 |
| EW (ew_all) | 29.4% | 27.0% | 29.1% | -36.9% | 0.97 | 0.04 | nan |
| DCA SMH | 38.0% | 32.3% | 32.1% | -39.6% | 1.04 | 0.00 | nan |
| DCA QQQ | 20.4% | 19.0% | 21.4% | -33.7% | 0.92 | 0.00 | nan |

### S&P 500 co-gate (2013-01→2026-08)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| V3 | 12.4% | 12.6% | 15.0% | -25.2% | 0.87 | 0.15 | -1.36 |
| EW (ew_all) | 15.3% | 15.0% | 15.7% | -28.2% | 0.97 | 0.03 | nan |

Random portfolios (1,000, same uptrend pool): percentile 97.7, 60th 26.9%

### Gates
- beats_b0r_is: no
- beats_b0r_oos: yes
- ge_ew_pit_oos: yes
- sp500_cogate: no
- ge_p60_random: yes
- **Ship: B0R**

**H2 in-sample was a null test.** All 96 IS months had < 40 eligible names (unscored), so every variant bought the same names as B0R and differed only in its exits; `beats_b0r_is` could hardly pass by construction. H2 is **inconclusive** (not "the variants failed"); the pre-registered decision (B0R stays) stands.

## H1 robustness (context only)
ADR-007a: reported, never used for selection. R-A = same dictionary, threshold ≥ 2 per 10k words; R-B = compute + network groups only, threshold ≥ 5. 2010-01→2026-08.

| Universe | names | rule (ew_trend10) | EW | DCA SMH | DCA QQQ | rule − EW | unscored months |
|---|---|---|---|---|---|---|---|
| ai_pit_ra | 307 | 29.5% | 36.5% | 29.7% | 19.5% | -7.0% | 79 |
| ai_pit_ra_stress | 616 | 29.9% | 28.9% | 29.7% | 19.5% | +1.0% | 26 |
| ai_pit_rb | 70 | 28.6% | 44.3% | 29.7% | 19.5% | -15.7% | 200 |
| ai_pit_rb_stress | 152 | 27.0% | 41.3% | 29.7% | 19.5% | -14.3% | 179 |

Eligible names per month-end (min / median / max):
| Year | ai_pit_ra | ai_pit_rb |
|---|---|---|
| 2010 | [0, 2, 5] | [0, 1, 2] |
| 2011 | [9, 11, 12] | [2, 4, 4] |
| 2012 | [14, 17, 19] | [5, 6, 7] |
| 2013 | [17, 17, 17] | [7, 7, 7] |
| 2014 | [25, 26, 26] | [13, 13, 13] |
| 2015 | [30, 32, 32] | [12, 13, 13] |
| 2016 | [38, 39, 42] | [23, 24, 24] |
| 2017 | [43, 44, 44] | [21, 21, 21] |
| 2018 | [48, 49, 50] | [16, 16, 16] |
| 2019 | [52, 57, 58] | [15, 18, 18] |
| 2020 | [61, 61, 64] | [20, 20, 20] |
| 2021 | [75, 77, 77] | [22, 22, 22] |
| 2022 | [83, 85, 85] | [27, 27, 27] |
| 2023 | [107, 108, 108] | [29, 29, 30] |
| 2024 | [125, 126, 129] | [31, 32, 32] |
| 2025 | [194, 196, 197] | [35, 35, 35] |
| 2026 | [220, 221, 222] | [38, 38, 38] |

## Bug-fix log

- 2026-09-30 (ADR-009a, review #2): F1: in unscored months (< 40 eligible) the simulator sold S2 trend failures and re-bought them at the same fill (equal weight into all eligible): 748 of 950 H1 rule sells were same-month re-buys. A name sold in a month is now never bought that month. F2: `listed` (Yahoo-priced) only via the CIK's current SEC ticker; index words (CRSP, NASDAQ, …) are no longer read as symbols. 8 PIT CIKs lost their Yahoo series and are now stress-test names: 6 carried another company's prices (Cray and Zix = CRISPR, Rockley = Worthington Steel, Lyris = NOV, CoreSite = Cencora, SGI = Somnigroup), ANSYS and the pre-2021 Marvell CIK have no current SEC ticker; Alphabet (GOOGL) and Rigetti (RGTI, not its warrant) were re-priced. H1, H1 robustness and H2 were re-run. The hand-picked IS/OOS/fidelity/sanity runs have 0 unscored months and were not re-run.
- 2026-09-30 (ADR-004d): the $3 eligibility floor was applied to split-adjusted closes, which uses future splits (lookahead) and wrongly excluded later winners (e.g. NVDA until ~2019). Floor removed; sanity, IS, OOS, fidelity and H1 were all re-run. Pre-fix results remain in git history.
- 2026-09-30 (ADR-004d): sanity (a) used ONE random-signal ranker, whose percentile is ~Uniform(0,100) under the null (a 35-65 band fails ~70% by chance). Now the mean percentile over 50 seeds. The EWU hand recomputation still applied the old $3 floor; aligned.
- Before the IS run: random portfolios changed to honor the ADR-004b group limit (`model.take_by_group`), so they differ from the ranked strategy only in *which* names are picked.

## Honesty
Absolute returns are an upper bound, because of survivorship and hindsight in the universe. About 92 OOS months cannot prove a modest edge. The lab can catch bugs, disasters and fragility. It cannot prove alpha.
