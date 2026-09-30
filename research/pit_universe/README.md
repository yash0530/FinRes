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
