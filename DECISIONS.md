# Decisions (ADR log)

Short, dated, append-only. Each ADR covers what was decided, why, and what we gave up.

---

## ADR-001 — Stack: FastAPI + Jinja/htmx + SQLite, Python 3.12 (2026-09-27)
**Decision.** The app runs as one Python process: FastAPI serves a single Jinja page; htmx (CDN, no build step) handles partial updates; storage is stdlib `sqlite3`; the LLM is called with `httpx`.
**Why.**
- yfinance, pandas and the LLM client are all Python, so everything stays in one language.
- There is no JS toolchain, and 2k lines is easy to read end to end.
**Rejected.**
- Streamlit: it reruns the whole script on every click, which is bad with multi-minute LLM calls.
- Next.js: it adds a second language, a build step and node_modules. The previous apps (ResearchApp, fin_research) are 38–40k LOC in that stack.
**Cost.** A little hand-written HTML and CSS.

## ADR-002 — Deliberate departure from Groundwork's "no signals, no recommendations" rule (2026-09-27)
**Context.** `personal_finance/latest_specs/HANDOFF-PROMPT.md` banned signal machinery, recommendations and backtesting. That was a reaction to bloat, not to evidence. FinRes has an explicit goal: help deploy $2,500/month into stocks for higher returns.
**Decision.** FinRes *does* rank stocks and propose buys and sells.
**Guardrails.**
- Only published, long-evidenced signals are used.
- They are validated once in a small `lab/`, with a grid and decision rule committed before any run (ADR-004).
- Every recommendation shows the raw numbers and the rule that produced it.
- The app tracks its own live picks against benchmarks.
**Kept from Groundwork.**
- Deterministic first: all math is plain code, and the LLM only narrates computed facts.
- A hard complexity budget.
- New ideas go to SOMEDAY.md.
- No schedulers or daemons.
**Explicitly not revived.** Debates, governors, tripwires, 13F, options, sentiment, alerts.

## ADR-003 — Data sources: yfinance + SEC EDGAR, both free (2026-09-27)
**Decision.**
- **Prices, estimates and news** come from yfinance 1.7. It impersonates a browser via curl_cffi internally. We never pass it a custom session, because requests-cache breaks it.
  - Downloads run in batches of about 40, with exponential backoff on `YFRateLimitError`.
  - Results are cached in SQLite.
  - Each ticker carries a staleness flag.
  - Stocks are not ranked unless at least 90% of the universe refreshed.
- **Fundamentals** come from SEC EDGAR companyfacts (XBRL), which is point-in-time.
  - Facts are keyed on `start`/`end`; the `fy`/`fp` fields describe the *filing*, not the period.
  - A fact is usable from the trading day after its `filed` date.
  - The latest value filed on or before the as-of date wins.
  - Duration tolerance covers 52/53-week fiscal years.
  - Requests run at no more than 8 per second with a User-Agent. Responses are cached gzipped under `data/edgar/`.
- **Foreign filers** (20-F/IFRS: TSM, ASML, …) fall back to yfinance `info` in the live app, flagged as not point-in-time. In the lab their quality factor is *missing*.

**Why.** Both sources are free and reliable enough, and EDGAR is the only free point-in-time fundamentals source.
**Known gap.** No free source has *historical* analyst estimates. Revisions can therefore only be validated forward. The app logs a weekly revisions snapshot for exactly that purpose.
**Rejected.**
- Finnhub, FMP, Alpha Vantage: free-tier limits are too low, or the endpoints we need are premium.
- Stooq: it now needs an API key.

## ADR-004 — Pre-registered validation: grid, rules, decision (2026-09-27, committed BEFORE any backtest run)
**Why this ADR exists.** The lab must not be tuned after seeing results. Everything the backtest can vary is fixed here. Nothing changes after the first run except bug fixes, and every bug fix is logged in `lab/REPORT.md`.

**Universes**
- **AI:** all `universe.toml` categories plus `[laggards]`. Laggards are ranked in the lab but not in the app, to reduce hindsight.
- **Co-gate:** current S&P 500 constituents (the list lives in fin_research's seeds).
- **Benchmarks:** SPY, QQQ, SMH.

**Calendar**
- In-sample (IS) runs 2013-01 → 2018-12; out-of-sample (OOS) runs 2019-01 → 2026-08.
- Signals are computed at each month-end close *t*. All trades execute at the close of the next trading day.
- Prices are yfinance adjusted closes, so dividends are included.

**Eligibility at t**
- At least 273 trading days of price history, and close ≥ $3.
- A month with fewer than 40 eligible names is **unscored**: its contribution goes equal-weight into every eligible name.

**Factors.** Percentiles are taken across eligible names at *t*.
- `Mom = 0.7·pct(r(t−252→t−21) / σ252) + 0.3·pct(close / max252)`. σ252 is the stdev of daily returns.
- `Qual = mean(pct(gross profit TTM / assets), pct(revenue TTM growth))`, from EDGAR, point-in-time. If both are missing, `Qual` is missing and `Mom` gets full weight.
- `Trend` passes when `close > SMA200` and `SMA50 > SMA200`.
- **Tie-break** is earnings yield: net income TTM ÷ (close × shares).
- **Speculative:** net income TTM < 0, or revenue missing.
- Revisions are **not** in the lab, because no free history exists. Live composite = `0.85·lab composite + 0.15·pct(Rev)`, labelled "unvalidated".

**Grid: 24 configs = 4 weights × 3 sells × 2 buy-counts**
- **Weights (Mom/Qual):** W1 0.8/0.2 · W2 0.6/0.4 · W3 1.0/0.0 · W4 0.4/0.6.
- **Sells** (evaluated at *t*):
  - **S1** never sell.
  - **S2** sell when close < SMA200 at two consecutive month-ends, OR price ≤ 0.65 × average cost.
  - **S3** is S2 plus a sell when the composite percentile is below 0.70.
- **Buy count:**
  - **Nvar** buys every name in the top 15% by composite that passes Trend and is not capped. N is capped at 10. If no name qualifies, the cash is carried to next month.
  - **N3** buys the top 3 by composite among names that pass Trend and are not capped.

**Fixed rules for every config**
- **Budget** = $2,500 + carried cash + this month's sale proceeds, split equally across the N buys. Fractional shares.
- **Brake:** when SPY < its SMA200, OR under 40% of eligible names are above their own SMA200, N is capped at 2.
- **Caps** (enforced once the portfolio is at least $25k):
  - No new money into a position ≥10% of the portfolio.
  - No new money into a group ≥30%.
  - No new money into a speculative position ≥3%, or while all speculative positions total ≥10%.
- **Costs:** 15 bps per side. Cash earns 0.

**Benchmarks**
- **B0, "equal-weight trend-gated" baseline:** each month's budget is split equally across all eligible names that pass Trend, and sells follow S2.
- **EWU:** the budget is split equally across all eligible names, with no sells.
- **DCA benchmarks:** the same $2,500 monthly into SPY, QQQ and SMH.

**Metrics**
- XIRR of the account (money-weighted; this is the primary metric).
- Time-weighted CAGR, volatility, max drawdown and Sharpe from monthly TWR.
- Excess over EWU.
- Newey-West t-statistic (lag 6) of monthly TWR excess vs EWU.
- Turnover.
- Eligible-name count for each month.

**Random portfolios**
- 1,000 simulations. Each month, each simulation draws N names uniformly from the **same** buy pool (Trend passes, not capped), using the same N, sells, caps and costs.
- Result reported: the best config's XIRR percentile among them.
- Context only: a variant that draws from all eligible names, with no gate.

**Selection (IS only)**
- Pick the config with the highest IS XIRR, excluding any config whose IS max drawdown is more than 10 percentage points worse than EWU's.
- Ties go to fewer sells, then the simpler config.

**OOS decision.** Only two configs are ever run OOS: the best IS config and B0. Ship the best config if and only if all three hold:
1. Its OOS XIRR is above B0's OOS XIRR (costs included).
2. Its OOS XIRR is at or above the **60th percentile** of random portfolios. The 75th percentile is also reported.
3. **S&P 500 co-gate:** the same config over 2013-01 → 2026-08 on the S&P 500 universe has XIRR ≥ EWU on that universe.

Otherwise ship **B0**, and the app says so.

**Sanity checks (must pass before results count)**
- A config that ranks by a random number lands between the 35th and 65th random-portfolio percentile.
- EWU's first 3 months match a hand calculation.
- Lookahead tests are green.

**Honesty.** Absolute returns are an upper bound, because of survivorship and hindsight in the universe. About 92 OOS months cannot prove a modest edge. The lab can catch bugs, disasters and fragility. It cannot prove alpha.

## ADR-004a — Amendment before any run: test the stock-level trend gate, point-in-time S&P membership (2026-09-27)
**Context.** An online literature pass (agy, `docs/research.md` addendum) argued two things:
- Per-stock 200-day filters whipsaw on volatile tech names, and the trend gate belongs at the market level.
- A point-in-time S&P 500 membership history exists for free (github.com/fja05680/sp500).

No backtest had been run when this amendment was written, so the pre-registration stays clean.

**Changes to ADR-004**
- **New grid dimension, gate:**
  - **G1:** buys require the stock's own Trend (as before).
  - **G0:** no per-stock gate. The market-level brake still applies, and S2/S3 sells are unchanged.
- **Weights W4 dropped.** It was quality-tilted and the weakest prior, and dropping it keeps the grid small.
- **Grid** = {W1, W2, W3} × {G1, G0} × {S1, S2, S3} × {Nvar, N3} = **36 configs**.
- **Random portfolios** draw from the same pool as the config under test, so the pool respects its gate.
- **Tie-break order:** fewer sells (S1 < S2 < S3), then G0 before G1 (simpler), then W3 < W1 < W2.
- **S&P 500 co-gate universe at month *t*** = names that were index members at *t* (fja05680 history) ∩ names with Yahoo prices. Delisted names have no Yahoo data, so some survivorship remains and is documented.
- **B0** is unchanged: equal-weight across stock-Trend-passing names, with S2 sells.

**Not adopted:**
- 12-2 lookback, FIP smoothness, earnings blackout: weak or unverifiable evidence for our setting. They are parked in SOMEDAY.
- Vol-weighted sizing: contradicts ADR equal weight (DeMiguel).
- ATR trailing stops: daily monitoring contradicts the once-a-month discipline.

## ADR-004b: At most 3 buys per cap group per month (2026-09-27, before any backtest run)
**Context.** The first live buy list (M5 smoke test) put 7 of 10 names in the semis group. The 30% group cap only starts once the portfolio reaches $25k, so the first ~10 months would have no diversification at all. No in-sample or out-of-sample result had been seen when this was decided. The lab was still downloading data.

**Decision.** `model.buy_list` walks the pool in tie-break order and skips any name whose cap group already has `MAX_PER_GROUP = 3` names in this month's list. It applies to every config, the random-portfolio draws that use `buy_list`, and the live app.

**Cost.** In a month where one theme dominates, the model buys lower-ranked names from other groups. That is diversification over conviction, and it is deliberate.

## ADR-004c: "Speculative" = losing money; missing data never blocks a label (2026-09-27, before any backtest run)
**Context.** In the M5 review, ASML showed "SPEC" and "Insufficient data". ASML files IFRS, so SEC has a few us-gaap facts for it but no revenue. ADR-004 made "revenue missing → speculative", which was meant to catch pre-revenue names such as OKLO, SMR and NNE. Those names also report net losses, so the loss test already catches them.
**Decisions.**
- **Speculative** means net income TTM < 0. When revenue is missing, the name is *unknown*, not speculative.
- **Live app:** when SEC has no revenue for a name, the Yahoo fallback is used (flagged "not point-in-time").
- **The live "2+ of quality/revisions/earnings-yield missing → Insufficient data" rule is removed.** Missing factors only re-weight, the same as in the lab. "Insufficient data" now only means not eligible: fewer than 273 days of history, a price under $3, or no recent price.

## ADR-005: Lab verdict, so FinRes ships B0 (equal-weight uptrend rotation), not the ranking (2026-09-27)
**Result** (`lab/REPORT.md`). The best in-sample config, W2-G1-S3-Nvar (19.9% XIRR in-sample vs 10.9% for EWU), **failed all three pre-registered out-of-sample gates**:
- 2019–2026 XIRR was 36.1%, vs 38.6% for B0 and 36.1% for EWU.
- It ranked at the 38.7th percentile of random portfolios, below the 60th-percentile bar.
- On point-in-time S&P 500 members over 2013–2026 it returned 8.4% vs 13.7% for EWU (NW t = −1.95).

Per ADR-004, **B0 ships**.

**What that means in plain words.** Within an AI universe, ranking stocks by risk-adjusted momentum and quality did not beat simply owning *all* of them that are in an uptrend. It did not beat picking at random from that uptrend pool either. The ranking added nothing we could measure. What did hold up in the AI universe is the discipline:
- equal weight
- only buy names in an uptrend (close > SMA200 and SMA50 > SMA200)
- sell after two month-ends below the 200DMA, or −35% from cost

**Shipped rules (app):**
- **Buy:** each month, $2,500 split equally across **10 names that are in an uptrend, least-held first** (the ones you own least of). At most 3 per cap group (ADR-004b). The composite rank only breaks ties between names you hold equally.
  - This is the practical form of B0. B0 itself would need 60–130 orders a month.
  - The post-hoc fidelity check shows it tracks B0 closely: 40.3% vs 38.6% out-of-sample, 29.2% vs 30.3% over 2013–2026 in the AI universe.
- **Sell (S2):** two consecutive month-ends below the 200DMA, or −35% from average cost. No rank-based sells.
- **No brake, no position or group caps.** B0 was validated without them, and rotating least-held first keeps positions roughly equal anyway. The regime line stays in the header as information only.

**How the ranking is presented now.** The grades and composite remain on the page as *research context*: why a stock is moving, and where it sits against its peers. They are labelled "not validated to beat equal weight". Estimate revisions stay live-only and unvalidated.

**Caveats you must know:**
1. **Hindsight.** The AI universe was chosen in 2026, so every absolute number above is inflated.
2. **Broad market.** On the S&P 500, the same trend gate *lagged* equal weight by about 2.5 points a year (11.1% vs 13.7%), with smaller drawdowns (−24% vs −29%). Its value in the AI universe may partly be the hindsight bias again.
3. **SMH.** Plain monthly DCA into SMH returned **39.9%** out-of-sample, as good as anything tested, with no work and a −39.6% max drawdown. Yash chose individual stocks only. That choice is his, and the app respects it, but the evidence does not say stock-picking in this app beats SMH.
4. **Power.** About 92 out-of-sample months can catch disasters, not prove an edge.

**Forward test.** The app logs every monthly pick against SMH and against the equal-weight universe (Track record). After 12 or more months, that is the real evidence.

## ADR-006: News and social media: SEC 8-K red flags only, as warnings (2026-09-27)
**Question (Yash).** Should FinRes use news, X/Twitter, Reddit, StockTwits?

**Evidence** (`docs/research.md` addendum 2).
- **Horizon.** News and social sentiment predict returns over hours to about 3 days, not months. Examples: Tetlock 2007, and Lopez-Lira & Tang, where the edge decays within 24–48 hours. Information ratios fall from about 1.5–2.0 at 1 day to below 0.2 at 30 days.
- **Size.** What predictability exists sits in small, illiquid stocks (Chen, Kelly & Xiu).
- **Retail attention is contrarian.** Robinhood herding stocks returned −4.7% over the next 20 days (Barber et al. 2022).
- **Replication.** The famous "Twitter mood predicts the Dow" result failed out of sample.
- **LLM backtests on historical news leak the future**, because the model has read what happened (Glasserman & Lin).
- **Access.** X costs about $0.005 per read and has no archive. StockTwits is enterprise-only. Reddit has no point-in-time history since Pushshift closed. None of these can be validated.

**Decision.**
1. **No sentiment factor and no X/Reddit/StockTwits.** They don't fit the monthly horizon, they're costly or fragile, and they can't be validated.
2. **Add SEC 8-K "hard news" red flags as warnings:**
   - **Items flagged:** 1.02 material agreement terminated, 1.03 bankruptcy, 3.01 delisting notice, 4.01 auditor change, 4.02 restatement.
   - **Window:** the last 45 days.
   - **Where shown:** on buys, holdings, categories and the Analyze card, and as a fact for Qwen.
   - **Why this source:** it's free, point-in-time and deterministic.
   - It is **not a rule**. These events are too rare in this universe to validate, so the buy rule stays ADR-005's, and the warning tells Yash to read the filing before buying.
3. Headlines stay where they are: 10 Yahoo headlines inside Explain, where Qwen reads them.
4. **Parked in SOMEDAY:** a Qwen headline red-flag scan of each month's buys, and a retail-attention (mention-spike) caution flag. Both need forward logging before they could ever count.

## ADR-007: Pre-registered hindsight-free re-test (2026-09-29, committed BEFORE any 10-K is scored)
**Why.** Every v1.0 lab number uses a universe picked in 2026, which is winner-biased. This test rebuilds, for each year, the set of companies that looked AI- or datacenter-exposed *at the time*, including companies that later died, and re-tests the shipped rule on it.

**Universe for year Y** (applied at every month-end rebalance t in Y):
- The company's latest 10-K was **filed in year Y−1**, by filing date, never by period date. Point-in-time means the filing date is before t.
- Its SIC code, as reported in that filing, is in one of these groups:
  - 3570–3579, 3612–3629, 3661–3669, 3670–3679, 3812, 3585, 7370–7379, 6798
  - utilities and uranium: 4911, 4931, 4991, 1094
- **Keyword score** is hits per 10,000 words, using `research/pit_universe/dictionary.json` (frozen, case-insensitive, word-boundary).
  - Non-utility groups need a total of **≥ 5** across compute + ai + network + power + cooling.
  - Utility/uranium groups need **≥ 2** from the power group alone.
- **Size:** TTM revenue ≥ **$100M**, point-in-time via `edgar.fundamentals(facts, t)`.
- **Price** ≥ **$3**, and ≥ 273 days of history (the existing eligibility rule).
- Companies are keyed by CIK, which is never reused. Tickers are mapped with date ranges, so a recycled ticker is resolved by date overlap.

**Prices.**
- Yahoo covers every company that still trades.
- For delisted companies, the **free Tiingo tier** is used as far as its quota allows. Priority goes to the names with the most eligible company-years.
- Anything left unpriced is reported, and it gets a **delisting stress test**: a delisting for performance is booked at −30% (NYSE/AMEX) or −55% (Nasdaq) at the last known price (Shumway 1997/1999). Mergers get no penalty.

**Tests** (same simulator and costs as ADR-004, $2,500/month):
- **H1** (one run, no selection), 2010-01 → 2026-08:
  - the shipped B0R + S2 rule
  - EW of the PIT universe
  - SMH DCA
  - QQQ DCA

  Reported three ways: priced names only, with the stress test applied, and against the 2026 hand-picked universe (for the bias estimate).
- **H2 variants.** In-sample is 2010-01 → 2017-12. The out-of-sample run covers 2018-01 → 2026-08 and happens once, only for the best in-sample variant:
  - **V1, Faber monthly.** Buyable while the month-end close is above the 10-month SMA of month-end closes. Sell after 2 consecutive month-ends below it. Otherwise the same as B0R.
  - **V2, buffer.** Enter when close > SMA200 × 1.02 and SMA50 > SMA200. Exit after 2 month-ends with close < SMA200 × 0.98.
  - **V3, FIP.** Start from the uptrend names. Keep the top third by 12-2 momentum, P(t−21)/P(t−252) − 1. From those, keep the lower half by `ID = sgn(PRET)·(%neg − %pos)` over the same daily window. Then least-held rotation, 10 names, ≤3 per group.
- **A variant replaces B0R only if ALL of these hold:**
  - it beats B0R in-sample AND out-of-sample
  - it does not lose to EW(PIT) out-of-sample
  - it passes the S&P 500 PIT co-gate (≥ EW over 2013–2026)
  - V3 only: it is at or above the 60th percentile of 1,000 same-pool random portfolios
- **If B0R's XIRR on H1 is below SMH DCA,** the app states it plainly and Yash decides (no automatic switch).
- **Robustness,** reported but never used for selection: a "generic tech" dictionary (compute + network groups only).

**Honesty (fixed text for the report).** The rule was chosen on data that overlaps 2019–2026. The PIT universe is a new *universe*, not new *time*. Vocabulary drifts ("big data" era, 2010–13), so the eligible counts for each year are reported, and years with fewer than 40 eligible names are unscored. The dictionary was written in 2026, so some hindsight remains in the choice of words. That is why it is frozen here before scoring, with a generic-tech robustness run.

## ADR-008: Budgets for month 2 (2026-09-29)
- The app stays at **≤ 1,500 LOC**. The new features (the `model.step` refactor, shadow ledgers and "what changed") are paid for by deleting dead code: `prices.suspicious_moves`, `signals.above200_at`, and `score(rng_signal)` moves to the lab.
- **`lab/` rises to ≤ 700 LOC.** New research code lives in **`research/` (≤ 450 LOC)**. Neither is ever imported by the app.
- **6th SQLite table `shadow(strategy, month, state_json)`** holds the forward shadow portfolios. That is one table for one clear purpose. The README scope table is updated.
- *Addendum (2026-09-29, Yash's request):* a **Guide** tab (`/guide`) renders `USER_GUIDE.md` inside the app, so the guide has a single source. It is documentation, not a second app page. It adds one dependency (`Markdown`) and about 6 lines of Python.

## ADR-004d: Bug fix, no price floor on split-adjusted closes (2026-09-30)
**Bug.** Eligibility required close ≥ $3, but the closes are **split-adjusted**, and adjustment uses splits that happen *after* t. NVDA's adjusted 2013 close is about $0.35, so NVDA (and AVGO, LRCX, KLAC, …) were wrongly ineligible for years. This is lookahead bias that systematically excluded future winners. It affected the v1 lab (ADR-004/005) and the first H1 run.

**Fix.** Remove the floor (`close > 0`). The revenue floor (ADR-007) and index membership already exclude penny stocks. Live prices are unaffected, since today's close equals the adjusted close.

**Protocol.** ADR-004 allows bug fixes if they are logged. Sanity, IS, OOS, fidelity and H1 were **all re-run** under the unchanged decision rules. Pre-fix results remain in git history (commit before this one) and are summarized in `lab/REPORT.md`'s bug-fix log. Whatever the re-run says ships, even if the ADR-005 verdict changes.

## ADR-007a: Robustness runs for H1 (declared 2026-09-30, before running them)
The frozen keyword threshold (≥ 5 per 10k words) made the point-in-time universe thin: fewer than 40 eligible names in most months, so the rule mostly behaves like equal weight. H1 alone therefore can't separate the rule from EW. These runs are **reported, never used for selection**:
- **R-A:** same dictionary, threshold ≥ 2 per 10k words.
- **R-B:** "generic tech" dictionary (compute + network groups only), threshold ≥ 5.

## ADR-009: Month-2 verdict (2026-09-30)
**Result** (`lab/REPORT.md`, pre-registered in ADR-004d/007/007a):
- **Hindsight-free universe, 2010–2026.** This is the list of companies that looked AI- or datacenter-exposed at the time, 250 companies in all.
  - The shipped rule returned **25.1%** XIRR, vs **29.7%** for SMH DCA and **44.0%** for equal weight across every eligible name, never selling.
  - With the delisting stress test: 25.4% / 29.7% / 41.2%.
  - With the broader universe (R-A, 323 names): rule 29.2% vs EW-hold 36.4% vs SMH 29.7%.
- **The rule trails EW-hold in every hindsight-free universe tested** and at best ties SMH. Its trend exits sell the rare 10× winners, such as AAOI, SMCI and NVDA, that drive thematic returns (Bessembinder skew).
- **Hand-picked 2026 universe (hindsight-biased), after the ADR-004d fix.** Out-of-sample, the rule returned 40.0% vs EW 36.9% vs SMH 39.9%. The rule beat EW in 10 of 14 years. So the rule's apparent edge appears only on the winner-picked list.
- **H2.** No variant (Faber monthly, 2% band, FIP) passed all gates, so **B0R stays shipped**. That is the pre-registered outcome, with no automatic switch.
- **Data quality.** In the spot check of 30 random "exposed" filings (`research/pit_universe/spot_check.md`), 15 are real, 3 partial and 12 boilerplate (SaaS vendors mentioning their own data centers or internal ML). The hindsight-free universe is honest but noisy. Tiingo was never keyed, so 59 delisted names are covered only by the stress test.

**What the app does now.**
- It keeps B0R + S2.
- It states this evidence in the Track record lab note and in the Guide, and the tech lead's opinion is labelled as such.
- It tracks three **forward shadow portfolios** (rule, equal-weight-hold, SMH) from 2026-09-30, so that hindsight-free *live* data settles the question in 6–12 months.

**TL opinion (Yash decides; not a rule change).**
- The honest evidence does not show that this app's stock rule beats monthly SMH buys.
- If you want individual stocks, "buy equal amounts of every uptrend name and don't sell on trend breaks" is at least as well supported as the current exits.
- The simplest robust choice is SMH.
- Watch the shadow portfolios before changing anything.

## ADR-009a: Review #2 fixes and the corrected verdict (2026-09-30)
An independent review (agy) found three bugs. The TL verified each against the data; kiro fixed them and re-ran every hindsight-free phase.
- **F1: same-month sell-and-rebuy.** In thin, unscored months, the lab sold a name on its trend exit and re-bought it the same day. This affected 748 of 950 sells.
  - Fix: a name sold this month is never re-bought that month.
  - The app now applies the same rule: a name on the Sell list is never in that month's buy list.
- **F2: wrong company's prices.** Filing-text symbols such as "CRSP" (an index name) mapped dead companies to live tickers: Cray was priced as CRISPR, Rockley Photonics as Worthington Steel, and so on.
  - Fix: a company counts as "listed" only through its *current SEC ticker*.
  - 16 statuses changed. Wrongly priced series were deleted; those companies became stress-test names.
- **F4: shadow ledgers and splits.** Shadow ledgers stored shares, which break when prices are re-adjusted after a split.
  - Fix: store the monthly decisions and replay them at current adjusted prices, which is split-proof.
- **Documented, not re-engineered:**
  - H2's in-sample test was a *null test*, because all 96 months were unscored, so H2 is **inconclusive**. B0R stays.
  - The stress clones' death dates come from after t (a stress assumption, not a signal).

**Corrected H1** (2010–2026 XIRR):

| | Rule | EW-hold | SMH DCA |
|---|---|---|---|
| Priced names | **26.6%** | **44.2%** | **29.7%** |
| With stress test | 25.6% | 41.2% | 29.7% |
| Broader universe (R-A) | 29.5% | 36.5% | 29.7% |

**The ADR-009 verdict stands:** without hindsight, the rule does not beat SMH, and it trails equal-weight hold. The review's claim that the fix lifts the rule to 32.5% did not reproduce: F1 alone gives 26.1%. That claim most likely came from its other suggestion (no trend sells in thin months), which drifts toward EW-hold.
