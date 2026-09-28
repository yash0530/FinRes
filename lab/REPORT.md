# FinRes validation lab — report

Method: pre-registered in [ADR-004 / ADR-004a](../DECISIONS.md) — 36 configs ({W1,W2,W3} × {G1,G0} × {S1,S2,S3} × {Nvar,N3}), IS 2013-01→2018-12, OOS 2019-01→2026-08, $2,500/month, signals at month-end close, fills at next-day close, 15 bps per side, cash earns 0.

## Data
- S&P 500 point-in-time membership: `lab/sp500_history.csv` from https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv (fetched 2026-09-27).
- ai: 145 tickers, 145 with prices, 140 with EDGAR facts.
- sp500: 815 tickers, 635 with prices, 618 with EDGAR facts.
- S&P members (as of mid-year) without Yahoo prices: {'2013': 120, '2014': 118, '2015': 109, '2016': 98, '2017': 87, '2018': 83, '2019': 72, '2020': 55, '2021': 47, '2022': 34, '2023': 24, '2024': 16, '2025': 12, '2026': 0}

## Sanity checks
- Random-signal ranker percentile: 41.1 (expect 35–65): pass
- EWU first 3 months match hand recomputation: pass
- Lookahead tests: pass

## In-sample (2013-01→2018-12), sorted by XIRR
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G1-S3-Nvar | 19.9% | 21.4% | 17.3% | -18.5% | 1.22 | 0.22 | 1.45 |
| W3-G0-S3-N3 | 19.0% | 20.4% | 17.6% | -19.7% | 1.15 | 0.32 | 1.95 |
| W2-G0-S2-Nvar | 17.6% | 19.1% | 16.9% | -20.5% | 1.13 | 0.15 | 1.16 |
| W2-G0-S3-N3 | 17.3% | 16.6% | 18.5% | -21.2% | 0.93 | 0.27 | 0.50 |
| W3-G0-S3-Nvar | 16.4% | 17.8% | 17.1% | -16.3% | 1.05 | 0.32 | 1.30 |
| W3-G1-S3-N3 | 15.6% | 17.7% | 17.2% | -16.7% | 1.04 | 0.32 | 1.16 |
| W2-G1-S3-N3 | 15.2% | 15.4% | 18.3% | -20.4% | 0.87 | 0.27 | 0.33 |
| W1-G0-S2-N3 | 15.1% | 15.2% | 15.7% | -18.8% | 0.99 | 0.16 | 0.30 |
| W1-G1-S2-N3 | 15.1% | 15.2% | 15.9% | -18.3% | 0.97 | 0.16 | 0.30 |
| W3-G0-S2-Nvar | 14.9% | 17.4% | 14.7% | -20.3% | 1.17 | 0.15 | 1.32 |
| W3-G1-S3-Nvar | 14.7% | 16.4% | 17.6% | -19.1% | 0.95 | 0.32 | 0.80 |
| W2-G0-S2-N3 | 14.5% | 14.1% | 17.2% | -19.8% | 0.86 | 0.16 | 0.12 |
| W2-G0-S3-Nvar | 14.2% | 16.7% | 17.9% | -20.5% | 0.96 | 0.25 | 0.67 |
| W3-G0-S2-N3 | 14.1% | 16.8% | 15.4% | -19.2% | 1.09 | 0.15 | 0.99 |
| W1-G1-S2-Nvar | 14.0% | 16.1% | 15.0% | -20.5% | 1.08 | 0.15 | 0.57 |
| W1-G0-S2-Nvar | 14.0% | 16.3% | 14.8% | -20.7% | 1.10 | 0.15 | 0.66 |
| W2-G0-S1-Nvar | 13.6% | 16.0% | 14.9% | -16.6% | 1.07 | 0.06 | 0.64 |
| W1-G1-S3-Nvar | 13.5% | 15.4% | 17.3% | -22.8% | 0.92 | 0.29 | 0.38 |
| W3-G1-S2-Nvar | 13.3% | 16.1% | 14.9% | -19.9% | 1.08 | 0.15 | 0.74 |
| W2-G1-S1-Nvar | 13.3% | 15.8% | 14.8% | -16.7% | 1.07 | 0.06 | 0.58 |
| W2-G0-S1-N3 | 13.3% | 13.7% | 15.7% | -16.9% | 0.90 | 0.06 | 0.00 |
| W2-G1-S2-N3 | 13.2% | 13.3% | 17.3% | -20.7% | 0.81 | 0.16 | -0.02 |
| W3-G1-S1-Nvar | 13.2% | 15.5% | 14.3% | -17.1% | 1.09 | 0.06 | 0.69 |
| W3-G1-S2-N3 | 13.2% | 16.1% | 15.2% | -19.0% | 1.06 | 0.15 | 0.77 |
| W3-G0-S1-Nvar | 13.2% | 15.5% | 14.2% | -17.0% | 1.09 | 0.06 | 0.73 |
| W1-G0-S1-Nvar | 13.2% | 15.0% | 14.1% | -16.6% | 1.07 | 0.06 | 0.37 |
| W1-G1-S1-Nvar | 13.1% | 15.0% | 14.1% | -16.5% | 1.06 | 0.06 | 0.34 |
| W2-G1-S1-N3 | 12.4% | 13.2% | 16.0% | -17.0% | 0.86 | 0.06 | -0.10 |
| W1-G0-S1-N3 | 12.2% | 13.5% | 15.0% | -17.0% | 0.92 | 0.06 | -0.11 |
| W1-G1-S1-N3 | 12.1% | 13.4% | 15.1% | -17.1% | 0.91 | 0.06 | -0.12 |
| W3-G0-S1-N3 | 11.9% | 14.4% | 15.1% | -16.3% | 0.97 | 0.06 | 0.23 |
| W2-G1-S2-Nvar | 11.7% | 15.4% | 16.7% | -19.4% | 0.95 | 0.15 | 0.40 |
| W3-G1-S1-N3 | 11.6% | 14.1% | 15.1% | -16.4% | 0.96 | 0.06 | 0.12 |
| W1-G1-S3-N3 | 11.3% | 12.2% | 17.1% | -21.0% | 0.76 | 0.33 | -0.27 |
| W1-G0-S3-Nvar | 11.1% | 13.5% | 17.3% | -23.1% | 0.82 | 0.30 | 0.02 |
| W1-G0-S3-N3 | 10.9% | 12.1% | 17.3% | -21.2% | 0.75 | 0.33 | -0.28 |
| EWU | 10.9% | 13.8% | 15.1% | -17.3% | 0.93 | 0.06 | nan |
| B0 | 13.7% | 16.1% | 14.8% | -16.8% | 1.09 | 0.16 | 1.20 |

**Selected:** W2-G1-S3-Nvar (highest IS XIRR with maxDD not >10 pp worse than EWU's).

## Out-of-sample (2019-01→2026-08)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G1-S3-Nvar | 36.1% | 36.1% | 34.9% | -35.9% | 1.06 | 0.21 | 0.36 |
| B0 | 38.6% | 38.0% | 37.2% | -31.0% | 1.05 | 0.15 | 0.85 |
| EWU | 36.1% | 34.1% | 30.7% | -32.1% | 1.12 | 0.05 | nan |
| DCA SPY | 16.7% | 16.4% | 17.1% | -23.8% | 0.98 | 0.00 | -2.56 |
| DCA QQQ | 20.6% | 21.7% | 21.7% | -33.7% | 1.02 | 0.00 | -1.94 |
| DCA SMH | 39.9% | 38.4% | 33.0% | -39.6% | 1.16 | 0.00 | 0.88 |

## Random portfolios (1,000 each)
| Variant | Selected percentile | Median | 60th (threshold) | 75th |
|---|---|---|---|---|
| Same pool (seed 11) | 38.7 | 38.0% | **39.9%** | 42.7% |
| All eligible, no gate (context, seed 13) | 49.0 | 36.2% | 38.3% | 42.1% |

## S&P 500 co-gate (point-in-time members, 2013-01→2026-08)
| Config | XIRR | CAGR | Vol | MaxDD | Sharpe | Turnover | NW t vs EWU |
|---|---|---|---|---|---|---|---|
| W2-G1-S3-Nvar | 8.4% | 8.2% | 18.0% | -26.7% | 0.53 | 0.21 | -1.95 |
| EWU | 13.7% | 13.7% | 15.5% | -28.6% | 0.91 | 0.03 | nan |

## Decision
- beats_b0: no
- ge_p60_random: no
- sp500_cogate: no
- **Ship: B0**

## Eligible names per year (min / median / max)
| Year | AI | S&P 500 |
|---|---|---|
| 2013 | [79, 81, 86] | [342, 347, 349] |
| 2014 | [86, 87, 88] | [349, 353, 357] |
| 2015 | [85, 86, 88] | [358, 365, 370] |
| 2016 | [86, 88, 91] | [372, 383, 391] |
| 2017 | [91, 92, 94] | [392, 401, 405] |
| 2018 | [94, 95, 96] | [406, 409, 418] |
| 2019 | [96, 99, 102] | [418, 420, 430] |
| 2020 | [100, 102, 108] | [431, 440, 443] |
| 2021 | [108, 110, 116] | [445, 450, 454] |
| 2022 | [116, 118, 120] | [455, 464, 471] |
| 2023 | [120, 124, 126] | [470, 475, 477] |
| 2024 | [124, 127, 131] | [478, 481, 485] |
| 2025 | [128, 134, 136] | [485, 489, 493] |
| 2026 | [137, 138, 140] | [494, 496, 500] |

## Implementation fidelity (post-hoc, not a selection)
B0 buys every uptrend name each month (~60–130 orders). The app ships the practical version `ew_trend10`: 10 uptrend names/month, least-held first (≤3 per group), S2 sells. It should track B0.
| Universe · period | B0 (ew_trend) | Practical (ew_trend10) | EWU |
|---|---|---|---|
| ai · IS | 13.7% (dd -17%) | 14.7% (dd -17%) | 10.9% (dd -17%) |
| ai · OOS | 38.6% (dd -31%) | 40.3% (dd -34%) | 36.1% (dd -32%) |
| ai · FULL | 30.3% (dd -32%) | 29.2% (dd -34%) | 26.1% (dd -32%) |
| sp500 · FULL | 11.1% (dd -24%) | 12.1% (dd -24%) | 13.7% (dd -29%) |

## Bug-fix log

- Before the IS run: random portfolios changed to honor the ADR-004b group limit (`model.take_by_group`), so they differ from the ranked strategy only in *which* names are picked.

## Honesty
Absolute returns are an upper bound, because of survivorship and hindsight in the universe. About 92 OOS months cannot prove a modest edge. The lab can catch bugs, disasters and fragility. It cannot prove alpha.
