# research/pit_universe — point-in-time 10-K scan (ADR-007)

Builds, per filing year, the set of companies whose own 10-K looked AI/datacenter-exposed at the time.
Rules and thresholds are in DECISIONS.md ADR-007; `dictionary.json` is frozen (a new dictionary needs a new ADR).
Never imported by the app.

```bash
.venv/bin/python -m research.pit_universe.scan index      # form.idx 2009Q1..2026Q3 -> data/tenk_index.csv
.venv/bin/python -m research.pit_universe.scan sic        # submissions JSON per CIK -> data/companies.csv, data/candidates.csv
.venv/bin/python -m research.pit_universe.scan score [--year 2024] [--limit 150]   # -> data/scores.csv
.venv/bin/python -m research.pit_universe.scan summary [--year 2024]               # -> scan_summary.md
```

- All requests go through `finres.edgar._get` (≤ 8 req/s, SEC User-Agent from `FINRES_SEC_UA`).
- Every step is resumable: raw files are cached under `data/idx/` and `data/sub/`, and `score` skips accessions already in `data/scores.csv`. Per-item failures go to `data/errors.csv`; rerun the step to retry them.
- `candidates.csv` is a prefilter on the company's *current* SIC (unknown SIC is kept). `score` then reads the SIC printed on the filing's own index page (`sic_src=filing`; falls back to current SIC only if the page shows none, `sic_src=current`). Only in-group filings have their primary document downloaded. Text is never stored.
- Word count = whitespace tokens after stripping tags, `<head>`, `<script>`, `<style>`, `<ix:header>` and HTML entities. Hits are case-insensitive, `\b`-bounded, any whitespace between phrase words; each dictionary term is counted separately.
- `eligible_text`: SIC 4911/4931/4991/1094 → `per10k_power ≥ 2`; other groups → `per10k_total ≥ 5`. `per10k_generic` (compute + network) is the robustness dictionary.
- `data/` is git-ignored.

## build.py — universe, tickers, facts, prices (ADR-007 H1)

```bash
.venv/bin/python -m research.pit_universe.build universe  # scores.csv -> data/eligible.csv (year, cik, name, sic, symbols_from_filing, filed)
.venv/bin/python -m research.pit_universe.build map       # -> universe.csv, coverage.md, data/unmapped.csv (review list)
.venv/bin/python -m research.pit_universe.build facts     # companyfacts per CIK -> data/facts/{cik}.json.gz (404 -> .404 marker)
.venv/bin/python -m research.pit_universe.build prices    # -> lab/data/lab.db prices as C{cik}; Tiingo if TIINGO_API_KEY is set
.venv/bin/python -m research.pit_universe.build report    # rewrite coverage.md incl. priced/unpriced
```

- **Year assignment.** Year Y uses the CIK's *latest* 10-K filed in Y−1 (filing date); it counts iff that filing has `in_group=1` and `eligible_text=1`. Years 2010–2026.
- **Tickers.** Candidates in order: symbols printed in that year's 10-K, symbols from the company's other 10-Ks (closest year first), current `tickers` from its submissions JSON, then each with a `Q` suffix (Tiingo renames bankrupt names, INAP → INAPQ). The first candidate with a Tiingo `supported_tickers` row (NYSE/NASDAQ/NYSE MKT/AMEX/NYSE ARCA, Stock, USD) overlapping the year wins; if a recycled ticker has several such rows, the row with the best date overlap (Jaccard) with the company's filing span (first 10-K → last 10-K + 365 d) wins. `status`: listed (Tiingo endDate ≥ 2026-09-01), delisted, unmapped.
- **Duplicates.** Several CIKs on the same Tiingo row in one year (utility subsidiaries in a combined 10-K, holding + operating company) keep one CIK: the one whose submissions list the ticker, then the earliest candidate, then the lowest CIK. The others are dropped and listed in `data/unmapped.csv` (`reason=duplicate ticker`).
- **Groups** from the filing SIC: semis {3670–3679, 3570–3579 except 3576}, network {3576, 3661–3669}, cloud {7370–7379}, infra {3612–3629, 3585, 3812, 4911, 4931, 4991, 6798, 1094}.
- **Facts** are fetched for every eligible CIK, mapped or not (the stress test applies the revenue floor to unpriced names too). No us-gaap facts (404/IFRS) → the $100M floor can't be applied → never a member.
- **Prices** are stored as `C{cik}` so a recycled ticker can't collide. Listed → Yahoo `period="max"`, kept from `tiingo_start`. Delisted → Tiingo `adjClose` (≤ 50/h, ≤ 1,000/day, resumable via `data/tiingo_done.csv`, most eligible years first, stops cleanly on the monthly-quota error). No `TIINGO_API_KEY` → Tiingo is skipped and every delisted CIK is listed in `data/unpriced.csv`. Yahoo has no delisted tickers.
- The lab (`python -m lab.run h1`) reads `universe.csv`, `data/facts/` and the `C{cik}` prices; unpriced company-years enter only the delisting **stress test** (`lab/run.py stress_closes`).

## classify.py — Qwen-cleaned universe (ADR-010)

```bash
.venv/bin/python -m research.pit_universe.classify extract    # Item 1 excerpts (anonymized) -> data/business/, business.csv  (~13 min, 210/min)
.venv/bin/python -m research.pit_universe.classify validate   # 30 hand-judged filings -> classify_validation.md
.venv/bin/python -m research.pit_universe.classify run        # local Qwen, one call at a time, resumable -> data/qwen_class.csv (~12.6 s/filing; 2,631 ≈ 9 h)
.venv/bin/python -m research.pit_universe.classify universe   # -> universe_q.csv, universe_ra_q.csv + per-year counts before/after
.venv/bin/python -m lab.run h1q --force && .venv/bin/python -m lab.run report   # ADR-010 section of lab/REPORT.md
```

- `run` needs Qwen up (`llm-serve start splash4`). Rows from 2026-10 on carry Qwen's `evidence` phrase (one line); earlier rows show "—".
- `extract` targets the filings behind the base and R-A company-years 2010–2026 (10-Ks filed 2009–2025). The yearly review adds the newest year's filings itself (`review --extract`).

## review.py — the once-a-year universe review (ADR-010 D)

Goal: don't miss a new AI winner. Each autumn, once most 10-Ks for the year are filed (example for 2027; `Y` = the year):

```bash
# 1. scan the new filings. First set scan.LAST = (Y, 3); delete data/sub/ so current tickers are fresh.
.venv/bin/python -m research.pit_universe.scan index           # new quarters only (~1 min)
.venv/bin/python -m research.pit_universe.scan sic             # submissions per CIK (~45 min at 7/s with data/sub/ deleted; seconds if cached)
.venv/bin/python -m research.pit_universe.scan score --year Y  # ~1,100 in-group 10-Ks (~6 min at 220/min)
# 2. classify only that year's filings that pass the other three conditions, plus the members' 10-Ks
.venv/bin/python -m research.pit_universe.review --year Y --extract   # Item 1 excerpts (~2 min); fetches missing companyfacts
.venv/bin/python -m research.pit_universe.classify run                # Qwen, ~12.6 s each (~200-300 filings ≈ 1 h)
# 3. review and edit by hand
.venv/bin/python -m research.pit_universe.review --year Y             # -> review_Y.md (seconds)
```

- **Candidate** = the CIK's latest 10-K filed in `Y` is R-A keyword-eligible (in-group SIC, ≥ 2 hits per 10k words; utilities/uranium: power hits), Qwen `sells_into` = true, PIT TTM revenue ≥ $100M (SEC companyfacts as of min(today, Y-12-31)), and its current SEC ticker is in no `universe.toml` category. Sorted by revenue.
- **Suggested category** from Qwen's role: compute → compute, semis_equipment → equipment, memory_storage → memory, networking → networking, datacenter_infra → datacenter, power_energy → power (SIC 49xx) or grid, cloud → hyperscalers (revenue ≥ $50B) or software, ai_software → software.
- **Removal review** (informational): members whose latest `Y` 10-K Qwen classifies `sells_into` = false.
- Filings not yet classified are listed at the top of `review_Y.md`. Nothing edits `universe.toml`: you do, by hand (new ideas → SOMEDAY.md first if unsure).
- Don't run `--extract` while a `classify run` is active: it appends to `business.csv`.
