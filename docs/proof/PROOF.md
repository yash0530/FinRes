# FinRes: proof that everything works (2026-09-27, commit `681a36d`)

Every user flow was driven **live**: real market data, real SEC data, real local Qwen 3.8. Each ran against a copy of the database, was asserted automatically, and then **every screenshot was checked by eye** by the tech lead (right-hand column).

| Check | Result |
|---|---|
| Live user flows (`python -m e2e.flows`) | **26/26 passed** |
| Unit/integration tests (`pytest -q`) | 95 passed, 1 warning in 4.16s |
| Lab↔app parity (identical composites and buys at 2 dates) | pass |
| Independent data check (NVDA, VST, TSM vs yfinance recomputation) | pass |
| Lab, out-of-sample 2019–26 (XIRR) | rule 40.3% · EW universe 36.1% · SMH DCA 39.9% (hindsight-biased universe) |

| # | Flow | Expected | Auto | TL by-eye check | Screenshot |
|---|---|---|---|---|---|
| 1 | cold-start | progress banner while refreshing; after ≤10 min the page reloads with the plan and all categories | ✅ | Progress bar with 'estimates 10/129' mid-refresh; after reload the plan and 11 categories render. | [01a-refresh-progress.png](01a-refresh-progress.png) [01b-after-refresh.png](01b-after-refresh.png) |
| 2 | header-regime | as-of date, data age, Qwen status, SPY vs 200DMA and % of universe in uptrend; no brake (ADR-005) | ✅ | Header shows data date + age, Qwen ready, regime pill; no brake wording. | [02-header.png](02-header.png) |
| 3 | plan | ≤10 uptrend buys, equal $ summing to $2,500 ±$1, each with 'you hold $X' and a reason; 'Rules: B0R · S2 — ADR-005'; no BUY/AVOID/Brake words on the page | ✅ | 10 uptrend names × $250 = $2,500, each with 'you hold $0' and a reason ending 'in uptrend'. | [03-plan.png](03-plan.png) |
| 4 | holdings-paste | table shows value, weight, P/L and a status for every row; sell list appears | ✅ | 7 pasted holdings with value/weight/P&L; AMD −50% → '-35% stop' (red), NXPI and COST → '2 month-ends below 200DMA' (COST checked by hand: 951.89 < SMA200 954.92 on 07-31, 943.89 < 957.64 on 08-31). | [04-holdings-paste.png](04-holdings-paste.png) |
| 5 | sells-and-rotation | sells = exactly the engineered S2 rules (−35% stop, 2 month-ends below 200DMA), no rank-based sells; the two held buy-list names are no longer at the top (least-held first) | ✅ | Sell list = AMD, COST, NXPI only; QCOM (rank F, still in uptrend) kept → no rank sells; STX/SNDK (now held) left the top of the buy list. | [05-sells-rotation.png](05-sells-rotation.png) |
| 6 | bad-holdings-line | error names line 1; nothing saved (DB + table unchanged) | ✅ | 'Nothing saved. Fix these lines: Line 1 …' and the table is unchanged. | [06-bad-holdings-line.png](06-bad-holdings-line.png) |
| 7 | mark-done | this month's picks rows appear in Track record; holdings now include every buy | ✅ | 10 buys added to holdings at their prices; track record rows share the pick date, so SMH and EW read +0% on day 0. | [07-mark-done.png](07-mark-done.png) |
| 8 | mark-done-twice | “Already recorded for YYYY-MM”; picks count in SQLite unchanged | ✅ | Second click: 'Already recorded for 2026-09.' | [08-mark-done-twice.png](08-mark-done-twice.png) |
| 9 | analyze-universe | card with grades, raw numbers, sparkline and reason | ✅ | NVDA card: returns, 1-year chart with dashed 200DMA, research factors captioned 'context, not the buy rule', UPTREND. | [09-analyze-nvda.png](09-analyze-nvda.png) |
| 10 | analyze-outside | card says “not in the universe” and is still graded | ✅ | COST card says 'not in the universe (percentiles vs universe)', graded, NO UPTREND, below 200DMA. | [10-analyze-outside-cost.png](10-analyze-outside-cost.png) |
| 11 | analyze-invalid | “No price data for ZZZZ”; validation error for BAD$$ | ✅ | 'No price data for ZZZZ' and a validation error for BAD$$. | [11a-analyze-no-data.png](11a-analyze-no-data.png) [11b-analyze-invalid.png](11b-analyze-invalid.png) |
| 12 | category-click | analyze card for that ticker loads and #analyze scrolls to the top of the viewport | ✅ | Clicking STX in Categories loads its card at the top of Analyze. | [12-category-click.png](12-category-click.png) |
| 13 | explain-live | polling state, then within 10 min a thesis card with Bull/Bear points + evidence keys, Qwen verdict vs rule label, grounding box | ✅ | Polling state with elapsed seconds, then a thesis card: bull/bear with evidence keys, 'All numbers traced to the data'. | [13a-explain-polling.png](13a-explain-polling.png) [13b-explain-card.png](13b-explain-card.png) |
| 14 | explain-cached | “cached this week” card in < 3 s | ✅ | Same card instantly with 'CACHED THIS WEEK'. | [14-explain-cached.png](14-explain-cached.png) |
| 15 | explain-busy | PLTR shows “Qwen is busy explaining VST”; VST then finishes so the slot is free | ✅ | PLTR shows 'Qwen is busy explaining VST (started 1 s ago)'. | [15-explain-busy.png](15-explain-busy.png) |
| 16 | explain-off | Qwen dot off + hint; Explain says “Qwen is off… llm-serve start splash4”; plan and analyze still work | ✅ | Header 'Qwen off — run llm-serve start splash4'; Explain shows the hint; the rest of the page works. | [16-explain-off.png](16-explain-off.png) |
| 17 | replay-2022 | replay banner; SPY below 200DMA and a low % in uptrend; buys = the few uptrend names then (0–10); Refresh disabled | ✅ | Replay 2022-09-30: 'SPY below 200DMA · 9% of universe in uptrend', 5 uptrend buys × $500, refresh disabled. | [17-replay-2022.png](17-replay-2022.png) |
| 18 | insufficient | row labelled “Insufficient data” with the reason (N trading days of history, need 273) | ✅ | CBRS row 'Insufficient data: 93 trading days of history (need 273)'. | [18-insufficient.png](18-insufficient.png) |
| 19 | empty-states | holdings empty-state text and track-record empty-state text | ✅ | Empty holdings and empty track record messages. | [19-empty-states.png](19-empty-states.png) |
| 20 | stale-coverage | “Ranking withheld” banner, no buy list, “stale” tags | ✅ | Banner 'Ranking withheld: only 84% …', plan hidden, 20 rows tagged STALE (12-1m still shown: cosmetic). | [20-stale-coverage.png](20-stale-coverage.png) |
| 21 | restart-persistence | holdings, picks and the cached thesis are all still there | ✅ | After restart: 17 holdings, 10 picks, cached thesis all present. | [21-restart-persistence.png](21-restart-persistence.png) |
| 22 | phone-width | no horizontal page scroll (scrollWidth ≤ 390); tables scroll inside their containers | ✅ | 390 px wide, no horizontal page scroll; buy rows wrap cleanly. | [22-phone-width.png](22-phone-width.png) |
| 23 | dark-mode | dark background, light text; plan + categories readable | ✅ | Dark theme readable: plan, sells (red), watch notes (amber). | [23-dark-mode.png](23-dark-mode.png) |
| 24 | parity | app path == lab path: same eligible set, trend flags, composite within 1e-9, identical B0R buy list | ✅ | No screenshot; see output. | – |
| 25 | sec-8k-flag | red 8-K tag on the row and on the analyze card's Watch line with the SEC 8-K warning | ✅ | OKLO row has a red 8-K tag; card Watch line 'SEC 8-K: material agreement terminated (2026-09-11)'. | [25-sec-8k-flag.png](25-sec-8k-flag.png) |
| 26 | data-check | close, 12-1 return and SMA200 from the app == independent yfinance recomputation for NVDA, VST, TSM | ✅ | No screenshot; independent recomputation matches the app exactly. | [data_check.json](data_check.json) |

### Flow 24 output (parity)
```
✓ exit 0: PARITY PASS
```

### Flow 26 output (data-check)
```
✓ exit 0
✓ NVDA 2026-09-25: close 225.07/225.07, 12-1 0.1862/0.1862, sma200 199.13/199.13 (app/independent)
✓ VST 2026-09-25: close 138.46/138.46, 12-1 -0.3041/-0.3041, sma200 155.16/155.16 (app/independent)
✓ TSM 2026-09-25: close 450.61/450.61, 12-1 0.4997/0.4997, sma200 381.35/381.35 (app/independent)
```

### User-guide dry run (2026-09-27, by the tech lead)
Followed USER_GUIDE.md word for word on a fresh `git clone` into /tmp with a new Python 3.12 venv. `pip install -r requirements.txt` installed cleanly. `pytest -q` gave 95 passed. `./run.sh` served the page, the first visit auto-refreshed, and the page then showed "Buy with $2,500 — 10 uptrend names × $250". Analyze NVDA rendered. The server log had zero errors or tracebacks.

Details for each flow (steps and every assertion) are in [results.json](results.json). To reproduce, see [e2e/README.md](../../e2e/README.md).
