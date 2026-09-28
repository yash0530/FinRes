# FinRes

A one-page local web app for investing **$2,500 a month in individual US AI and AI-adjacent stocks**. You open it once a week. It tells you:

- **What to buy this month:** 1–10 names, depending on signal strength, each with a one-line reason.
- **What to sell** from your holdings, and which rule fired.
- **Why any stock ranks where it does:** momentum, quality, estimate revisions and trend, with the raw numbers.
- **A grounded thesis on demand** from a local Qwen 3.8 model. The app works fully without it.

See **[USER_GUIDE.md](USER_GUIDE.md)** for how to use it.

## Honest framing
No app can guarantee profit. FinRes aims for the next best thing:
1. It ranks only on signals with decades of published evidence: risk-adjusted 12-1 momentum, nearness to the 52-week high, profitability, estimate revisions, and a 200-day trend gate.
2. It tests those signals on past data (`lab/`) with a pre-registered decision rule, before trusting them.
3. It enforces a fixed, boring monthly buy/sell discipline. That is where retail investors actually gain an edge.
4. It measures its own live picks against SMH and an equal-weight universe, so we find out whether it helps.

## Scope rules (enforced)
FinRes exists because five earlier apps died of bloat. These limits are the product:

| Limit | Value |
|---|---|
| Pages | 1 |
| SQLite tables | 5 |
| Schedulers / daemons | 0 |
| LLM roles | 1 (on-demand thesis writer) |
| Processes | 1 (`./run.sh`) |
| `finres/*.py` | ≤ 1,500 lines (`tests/test_budget.py` fails the build) |
| templates + CSS | ≤ 600 lines |
| `lab/` | ≤ 600 lines, never imported by the app |

**Out of scope:** multi-agent debates, portfolio optimizers, options, 13F, insider, sentiment, alerts, auth, cloud deploy, broker connections, taxes.
**New ideas** go to [SOMEDAY.md](SOMEDAY.md) first.
**Design decisions** are in [DECISIONS.md](DECISIONS.md). Research notes are in [docs/research.md](docs/research.md).

## Run
```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                      # → http://127.0.0.1:8500
llm-serve start splash4       # optional: enables "Explain with Qwen"
```
