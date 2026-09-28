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
