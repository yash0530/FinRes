# Research notes (2026-09-27)

These are condensed findings behind FinRes's design. The evidence comes mostly from broad US equities, so how well it transfers to a thematic AI basket is an assumption, and the `lab/` exists to test that assumption.

## Signals with evidence
- **Momentum (12-1 month).** This is the canonical signal. In long-only large caps the gains come from the winners: an S&P 500 test over 2006–2024 found +7.9%/yr from the long leg ([SSRN 5367656](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5367656)). Several refinements help:
  - Momentum *crashes* happen in sharp rebounds after bear markets: −73% in 2009 ([Daniel & Moskowitz](https://alphaarchitect.com/avoiding-momentum-crashes/)).
  - Scaling by volatility roughly doubles Sharpe: [Barroso & Santa-Clara](https://alphaarchitect.com/risk-of-momentum-crashes/) do it at the portfolio level. FinRes uses the stock-level cousin, *risk-adjusted momentum* (return ÷ own volatility).
  - Residual momentum lowers crash risk ([Blitz, Huij & Martens](https://www.ssrn.com/abstract=2319883)).
  - Nearness to the 52-week high predicts returns and does not reverse ([George & Hwang](https://www.bauer.uh.edu/tgeorge/papers/gh4-paper.pdf)).
- **Estimate revisions.** Price momentum and earnings momentum each predict returns on their own, and analysts adjust slowly ([Chan, Jegadeesh & Lakonishok 1996](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1996.tb05222.x)).
  - The drift lasts 3–12 months and is stronger with low analyst coverage.
  - The best measures are revision breadth (up − down) / total, and the 3-month % change in the FY1/FY2 estimate ([Guerard/FactSet](https://go.factset.com/hubfs/Symposium%20Images/Guerard_EARNINGS%20FORECASTS%20AND%20REVISIONS,%20PRICE%20MOMENTUM,%20AND%20FUNDAMENTAL%20DATA.pdf?hsLang=en)).
  - Zacks Rank is built on revisions.
- **Post-earnings drift is not used.** It is roughly zero for large caps since about 2006 ([Martineau](https://www.researchgate.net/publication/362596818_Rest_in_Peace_Post-Earnings_Announcement_Drift)).
- **Profitability.** Gross profit ÷ assets is persistent and works in large long-only portfolios ([Novy-Marx](https://mysimon.rochester.edu/novy-marx/research/OSoV.pdf)). It is weakly correlated with momentum, so combining the two reduces extreme losses ([AQR](https://images.aqr.com/-/media/AQR/Documents/Insights/White-Papers/AQR-A-New-Core-Equity-Paradigm.pdf)).
- **Trend gate.** Holding only while price is above the 10-month / 200-day average sharply cut drawdowns ([Faber](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461)). That evidence is for asset classes; nobody has tested it per stock.
- **Value** is the weakest signal inside hot themes, so FinRes uses it only as a tie-break.

## Process and discipline
- **Equal weight.** 1/N beats 14 optimized allocation models out of sample ([DeMiguel, Garlappi & Uppal 2009](https://academic.oup.com/rfs/article-abstract/22/5/1915/1592901)).
- **Buffered entry and exit.** Buying in the top zone and selling only after dropping well below it cut churn by about 52% ([SSRN 7389238](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7389238)).
- **Stop-losses.** They only help when prices have momentum ([Kaminski & Lo](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338)). FinRes checks once a month and has one catastrophic stop, at −35%.
- **Overtrading.** The most active retail traders earned 11.4% vs 17.9% for the market ([Barber & Odean](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=219228)).
- **Mechanical following.** Magic Formula clients who picked stocks themselves made 59% vs 84% for those on autopilot ([Greenblatt](https://www.gurufocus.com/news/175426/joel-greenblatt-on-how-and-why-investors-struggle-to-follow-the-magic-formula)).
- **Skewed outcomes.** About 4% of stocks created all net wealth ([Bessembinder](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2900447)). You need enough names to catch the rare big winners.
- **Correlated universe.** When stocks move together, adding names beyond about 15–20 barely reduces risk. The theme itself is the risk, which is why FinRes applies group caps.

## Regime (Sep 2026)
- Hyperscaler capex is about $730B (+78%) ([TMT Finance](https://www.tmtfinance.com/intel/2026-hyperscaler-capex-tops-us700bn-analysis)).
- The memory selloff in July took memory stocks down about 30% ([24/7 Wall St](https://247wallst.com/investing/2026/07/28/ai-memory-boom-goes-bust-micron-sk-hynix-sandisk-plunge-30-and-are-still-falling/)).
- The 10-year yield is above 5%, the highest since 2007 ([Bloomberg](https://www.bloomberg.com/news/articles/2026-09-15/us-10-year-treasury-yields-rise-to-highest-level-since-2007)).
- Comparisons to 1999 are common: AI megacaps are over 30% of the S&P 500 ([IntuitionLabs](https://intuitionlabs.ai/articles/ai-bubble-vs-dot-com-comparison)).
- **Implication:** sub-themes swing 30% or more, which is the case for a trend gate, group caps and a market brake.

## Validation method
- **Survivorship bias.** A universe picked in 2026 is winner-biased; survivorship typically adds 1–4%/yr. FinRes therefore judges *relative* results (vs an equal-weight portfolio of the same universe), adds a laggards list, and uses the S&P 500 as a co-gate.
- **Random-portfolio benchmark.** Built with the same constraints, it is the right skill yardstick ([Burns](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=630123)).
- **Multiple testing.** A t > 3 bar is recommended ([Harvey, Liu & Zhu](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824)). About 92 out-of-sample months cannot prove a modest edge (t ≈ 0.55 for 3%/yr at 15% tracking error). The lab catches bugs and disasters; it does not prove alpha.
- **Old backtest.** The 46.7% "coin-flip" backtest in the old fin_research tested *1-day spikes* (a reversal effect) against a pooled mean, with overlapping samples and a calendar-day horizon. It says nothing about 12-1 momentum.

## Data (free, 2026)
- **yfinance 1.7** (curl_cffi): prices, `eps_trend`, `eps_revisions`, `info`, `news`. HTTP 429 blocks are common, so it needs batching, backoff and caching.
- **SEC EDGAR companyfacts:** free, 10 requests/second, User-Agent required, facts carry a `filed` date so they are point-in-time. Historical analyst estimates are paid-only.
- **Local LLM:** `llm-serve start splash4` runs LM Studio at `127.0.0.1:8089/v1`, model `qwen-local`. `reasoning_effort` accepts low, medium or xhigh (the template default is xhigh). There is one slot. Health is checked with `GET /v1/models`.

## Addendum — agy literature pass (2026-09-27)
The full notes are kept out of the repo. Claims are graded by how verifiable they were.
- **Credible, adopted as tests (ADR-004a):**
  - A per-stock 200DMA gate may whipsaw on 40–80%-vol names. Faber's evidence is index-level only.
  - Point-in-time S&P 500 membership is available at [fja05680/sp500](https://github.com/fja05680/sp500).
  - yfinance has no delisted history (checked: SIVB, TWTR, FRC return no data).
- **Credible, parked:**
  - Alpha Architect QMOM uses 12-2 momentum plus frog-in-the-pan smoothness ([Da, Gurun & Warachka 2014](https://academic.oup.com/rfs/article/27/8/2171/1587635)).
  - Residual momentum ([Blitz et al.](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1777038)).
- **LLM stock picking:**
  - No credible out-of-sample live evidence exists for open-source LLM hedge funds (e.g. virattt/ai-hedge-fund).
  - Headline-sentiment alpha decayed quickly after 2021 ([Lopez-Lira & Tang](https://arxiv.org/abs/2304.07619)).
  - This supports keeping Qwen as a *narrator* of computed facts, not a signal.
- **Rejected or unverifiable:**
  - an "earnings blackout" paper whose citation was a bare ssrn.com link
  - claims of a July-2024 selloff mechanism as evidence for our design
  - vol-weighted sizing and ATR stops, which conflict with ADR-004 principles
