# FinRes: user guide

FinRes tells you where this month's **$2,500** goes, across AI and AI-adjacent US stocks, and what to sell. It explains any stock on demand. It is one page, it runs locally, and it takes about 2 minutes a week plus 10 minutes a month.

## Start
```bash
cd ~/Desktop/Programming/FinRes
./run.sh                       # open http://127.0.0.1:8500
llm-serve start splash4        # optional: enables "Explain with Qwen"
```
The first visit of the week refreshes the data automatically (about 1.5 minutes, with a progress bar). Stop the app with Ctrl-C.

## The rules (what the app actually does)
| | Rule | Why |
|---|---|---|
| **Buy** | Split $2,500 equally across **10 stocks in an uptrend**, choosing the ones you hold *least* first. No more than 3 from one group (semis, cloud, network, infra). | In the lab (2013–2026), owning all uptrend names equally did as well as or better than any ranking we tested ([ADR-005](DECISIONS.md)). |
| **Uptrend** | Price is above its 200-day average, *and* the 50-day average is above the 200-day. | This filter avoids buying falling stocks. |
| **Sell** | (a) The stock closed below its 200-day average at **two month-ends in a row**, or (b) it is **−35%** from your average cost. | Cuts the losers, lets winners run, and checks once a month so you don't overtrade. |
| **Warning** | A red **8-K** tag means the company filed a serious SEC event in the last 45 days: bankruptcy, delisting notice, auditor change, restatement, or a terminated major agreement. | Rare but serious. **Read the filing before buying.** |

Everything else on the page, including grades, "Rank" and Qwen's opinion, is **research context**. It helps you understand a stock. It does not change the rules.

## Weekly (2 minutes)
1. Open the page and let it refresh if it's older than 6 days.
2. Glance at **Sell** and any **8-K** tags on your holdings.
3. If you're curious about a stock, type it into **Analyze**. For a written bull/bear case, click **Explain with Qwen**. It takes 2–7 minutes (Qwen reasons at xhigh) and is cached for the week.

## Monthly (in the first days of each month, ~10 minutes)
1. Open the page and refresh if needed. **This month's plan** lists up to 10 names at $250 each, with approximate share counts.
2. If a buy has an **8-K** warning, open that stock in Analyze, read the flagged filing, and decide whether to skip it.
3. Place the orders at your broker. Fractional shares are fine.
4. Click **"I placed these buys"**. That records them in your holdings and in the Track record.
5. Sell anything in the **Sell** list, then click **Mark sold** for each one.
6. If your broker fill prices differ, fix them with **Edit holdings**. One line per position: `TICKER SHARES AVG_COST`, for example `NVDA 12 131.40`.

## Reading the page
- **Header:** data date and age, Refresh button, Qwen status, market context (SPY vs its 200-day average, and the % of the universe in an uptrend). The market context is information only.
- **Holdings:** value, weight, profit/loss, the rule status, and the sell rule if one has fired.
- **Analyze:** price, returns, distance from the 52-week high, a 1-year chart with the 200-day average, and research factors graded A–F against the universe:
  - **Momentum:** risk-adjusted 12-month return, excluding the last month, plus nearness to the 52-week high.
  - **Quality:** gross profit ÷ assets and revenue growth, from SEC filings.
  - **Revisions:** whether analysts have been raising estimates. This one is live-only and unvalidated.
  - **Rank:** the blend of the three. *In the lab it did not beat equal weight*, which is why it only breaks ties.
- **Categories:** all 129 stocks in 11 groups, with how many are in an uptrend. Click a ticker to analyze it. The tag **SPEC** means the company is losing money. **stale** means the data is old.
- **Track record:** your recorded monthly buys vs SMH and the equal-weight universe over the same window, plus the lab headline.

## What to expect (honest)
- **Backtest** (`lab/REPORT.md`, 2019–2026 out-of-sample):
  - the rule: about 40% a year
  - the equal-weight universe: 36%
  - monthly buys of SMH: 40%

  These numbers are **inflated**, because the universe was chosen in 2026 with hindsight.
- **The rule is not magic.** It beat equal weight in 8 of 14 years. It lagged badly in 2025 and so far in 2026 (+7% vs SMH +46%), because trend filters whipsaw in choppy markets.
- **SMH, a single ETF, did about as well** with zero effort. You chose individual stocks, and that's fine. Just know the evidence doesn't say this app beats SMH.
- **The biggest edge is discipline:** same rules every month, no cherry-picking. Investors who overrode a ranking system did worse than those who followed it.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Qwen is off" | `llm-serve start splash4`, then click Explain again. |
| "Qwen is busy" | One explanation runs at a time. Wait for it to finish. |
| Refresh errors / "ranking withheld" | Yahoo rate-limited you. Wait 30–60 minutes and click Refresh. The page still shows the older data. |
| SEC requests fail (403) | Set a contact email for SEC: `export FINRES_SEC_UA="Your Name you@email.com"`, then run `./run.sh` again. |
| Want different stocks | Edit `universe.toml`: add or remove tickers in any category, then Refresh. |

**More detail:**
- [DECISIONS.md](DECISIONS.md): every design decision and the reason for it.
- [lab/REPORT.md](lab/REPORT.md): the full backtest.
- [docs/proof/PROOF.md](docs/proof/PROOF.md): every flow verified, with screenshots.
- [SOMEDAY.md](SOMEDAY.md): parked ideas.
