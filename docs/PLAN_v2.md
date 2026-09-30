# FinRes v2 plan (self-approved 2026-09-30; 3-month budget)

## Where we are (v1.1)
- The shipped rule is B0R: 10 uptrend names a month, least-held first, with trend sells (S2).
- **Without hindsight it did not beat SMH:** 26.6% vs 29.7% XIRR. It trailed equal-weight buy-and-hold (44.2%), apparently because the trend sells cut the rare 10× winners.
- The hindsight-free universe is honest but noisy: about 40% of the "AI-exposed" matches are boilerplate.
- Forward shadow portfolios (rule, EW-hold, SMH) start 2026-09-30.

## Principle (Yash, 2026-09-30: single page, simplicity first, focus only on making money)
Months of work don't create edge. v2 spends its budget on **(1) cleaner evidence, (2) one well-tested decision, and (3) making the monthly ritual faster and safer.** Hard rules still apply:
- app ≤ 1,500 LOC
- 1 page (plus the Guide tab)
- no daemons
- one LLM role in the app; offline research may use Qwen

## Order of work: B (money decision) → A (evidence quality) → D (don't miss new winners) → E

## Phase A: cleaner hindsight-free universe (ADR-010)
- For every eligible 10-K (base + R-A universes), extract Item 1 "Business" (first ~1,500 words) and anonymize it by replacing the company's names and tickers with "the Company".
- Local Qwen classifies each extract: does the Company **sell** products or services into AI, datacenters, accelerators, datacenter networking, storage, power or cooling? The output is strict JSON with a role and a confidence.
- Validate against the 30 hand-judged filings from the agy spot check. The target is agreement of at least 80% on REAL vs BOILERPLATE.
- **Pre-registered:** `universe_q` = eligible_text AND Qwen says it sells in. Re-run H1 and R-A on `universe_q`. The results are reported, not used for selection.

## Phase B: one decision, well tested (ADR-011, pre-registered before any run)
- **Candidate B0H** ("buy uptrend, hold"): the same buys as B0R, but it **never sells on a trend break**. It sells only on the −35% catastrophic stop.
- **Switch rule:** ship B0H instead of B0R only if B0H ≥ B0R (XIRR) on **all five** test sets:
  1. the hand-picked universe, OOS 2019–26
  2. PIT priced, 2010–26
  3. PIT with the stress test
  4. R-A
  5. the S&P 500 PIT co-gate, 2013–26

  It must also stay within 10 pts of B0R's max drawdown on each set. Otherwise B0R stays.
- B0H joins the forward shadow ledgers either way.

## Phase C: CUT (2026-09-30, Yash: "single page, simplicity first, focus only on making me money")
The order ticket, fill recording and risk line are conveniences, not returns, so they are not built.

## Phase D: keep the universe honest going forward
- `python -m research.pit_universe.review` scans the latest year's 10-Ks and lists **candidate additions**. These are companies that pass the text + Qwen screen and are not in `universe.toml`, each with its role and evidence line. Yash approves by editing `universe.toml`. It runs once a year; no automatic changes.

## Phase E: verify and ship
- Two independent reviews (agy), each verified by the TL before any fix. Fixes go through kiro.
- e2e flows for every new feature, and the TL checks every screenshot.
- Fresh-clone dry run of the guide; USER_GUIDE, README and DECISIONS updated; tag **v2.0**.
- A forward shadow-portfolio status report with whatever real months exist by then, clearly labelled too early to decide.

## Explicitly not doing
- Sentiment feeds
- More LLM roles in the app
- Daemons or alerts
- Broker APIs
- Taxes
- Re-tuning B0R on data it has already seen
