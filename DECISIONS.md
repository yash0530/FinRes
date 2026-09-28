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
