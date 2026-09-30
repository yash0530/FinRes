# FinRes

A one-page local web app for investing **$2,500 a month in individual US AI and AI-adjacent stocks**. You open it once a week. It tells you:

- **What to buy this month:** up to 10 stocks in an uptrend at equal dollars, starting with the ones you hold least.
- **What to sell** from your holdings, and which rule fired.
- **Research context for any stock:** momentum, quality, estimate revisions, trend and SEC 8-K red flags, with the raw numbers.
- **A grounded bull/bear thesis on demand** from a local Qwen 3.8 model. The app works fully without it.

See **[USER_GUIDE.md](USER_GUIDE.md)** for how to use it. For proof that every flow works, with screenshots, see **[docs/proof/PROOF.md](docs/proof/PROOF.md)**.

## What the evidence said (short version)
- **v1:** a momentum and quality ranking did not beat owning every uptrend stock equally. The app therefore ships the simple rule: **$2,500 across 10 uptrend names, least-held first; sell after 2 month-ends below the 200-day average or at −35%** ([ADR-005](DECISIONS.md)).
- **Month 2, hindsight-free re-test:** the universe is rebuilt each year from 10-K text, so it includes companies that later died. Over 2010–26 the rule returned 25% XIRR, **SMH 30%**, and equal-weight buy-and-hold **44%**. The rule's edge only appears on the hand-picked 2026 list ([ADR-009](DECISIONS.md), [lab/REPORT.md](lab/REPORT.md)).
- The app keeps the rule, shows this plainly, and runs **forward shadow portfolios** (rule vs equal-weight-hold vs SMH) so live data can decide.
- News, X and Reddit sentiment has no monthly edge for large caps. The app only flags serious SEC 8-K events ([ADR-006](DECISIONS.md)). Local Qwen writes grounded bull/bear notes, and every number is traced back to the data.

## Honest framing
No app can guarantee profit. FinRes aims for the next best thing:
1. It starts from signals with decades of published evidence: risk-adjusted 12-1 momentum, nearness to the 52-week high, profitability, estimate revisions, and a 200-day trend gate.
2. It tests them on past data (`lab/`) with a pre-registered decision rule, and it ships whatever survives. In the end that was the trend gate with equal weights, not the ranking.
3. It enforces a fixed, boring monthly buy/sell discipline. That is where retail investors actually gain an edge.
4. It measures its own live picks against SMH and an equal-weight universe, so we find out whether it helps.

## Scope rules (enforced)
FinRes exists because five earlier apps died of bloat. These limits are the product:

| Limit | Value |
|---|---|
| Pages | 1 |
| SQLite tables | 6 (prices, snapshot, thesis, holdings, picks, shadow) |
| Schedulers / daemons | 0 |
| LLM roles | 1 (on-demand thesis writer) |
| Processes | 1 (`./run.sh`) |
| `finres/*.py` | ≤ 1,500 lines (`tests/test_budget.py` fails the build) |
| templates + CSS | ≤ 600 lines |
| `lab/` | ≤ 850 lines, never imported by the app |
| `research/` | ≤ 450 lines, never imported by the app |

**Out of scope:** multi-agent debates, portfolio optimizers, options, 13F, insider, sentiment, alerts, auth, cloud deploy, broker connections, taxes.
**New ideas** go to [SOMEDAY.md](SOMEDAY.md) first.
**Design decisions** are in [DECISIONS.md](DECISIONS.md). Research notes are in [docs/research.md](docs/research.md).

## Run
```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                      # → http://127.0.0.1:8500
llm-serve start splash4       # optional: enables "Explain with Qwen"
```
