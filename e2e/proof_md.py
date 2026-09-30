"""Render docs/proof/PROOF.md from results.json, data_check.json, pytest output and the lab results.

Run after `python -m e2e.flows`:  .venv/bin/python -m e2e.proof_md
`TL_CHECK` holds the tech lead's by-eye verification of each screenshot (done manually, not by the script).
"""
import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "docs" / "proof"

TL_CHECK = {
    1: "Progress bar with 'estimates 10/129' mid-refresh; after reload the plan and 11 categories render.",
    2: "Header shows App/Guide tabs, data date + age, Qwen ready, regime pill; no brake wording.",
    3: "10 uptrend names × $250 = $2,500, each with 'you hold $0' and a reason ending 'in uptrend'.",
    4: "7 pasted holdings with value/weight/P&L; AMD −50% → '-35% stop' (red), NXPI and COST → '2 month-ends below 200DMA' (COST checked by hand: 951.89 < SMA200 954.92 on 07-31, 943.89 < 957.64 on 08-31).",
    5: "Sell list = AMD, COST, NXPI only; QCOM (rank F, still in uptrend) kept → no rank sells; STX/SNDK (now held) left the top of the buy list.",
    6: "'Nothing saved. Fix these lines: Line 1 …' and the table is unchanged.",
    7: "10 buys added to holdings at their prices; track record rows share the pick date, so SMH and EW read +0% on day 0.",
    8: "Second click: 'Already recorded for 2026-09.'",
    9: "NVDA card: returns, 1-year chart with dashed 200DMA, research factors captioned 'context, not the buy rule', UPTREND.",
    10: "COST card says 'not in the universe (percentiles vs universe)', graded, NO UPTREND, below 200DMA.",
    11: "'No price data for ZZZZ' and a validation error for BAD$$.",
    12: "Clicking STX in Categories loads its card at the top of Analyze.",
    13: "Polling state with elapsed seconds, then a thesis card: bull/bear with evidence keys, 'All numbers traced to the data'.",
    14: "Same card instantly with 'CACHED THIS WEEK'.",
    15: "PLTR shows 'Qwen is busy explaining VST (started 1 s ago)'.",
    16: "Header 'Qwen off — run llm-serve start splash4'; Explain shows the hint; the rest of the page works.",
    17: "Replay 2022-09-30: 'SPY below 200DMA · 9% of universe in uptrend', 5 uptrend buys × $500, refresh disabled.",
    18: "CBRS row 'Insufficient data: 93 trading days of history (need 273)'.",
    19: "Empty holdings and empty track record messages.",
    20: "Banner 'Ranking withheld: only 84% …', plan hidden, 20 rows tagged STALE (12-1m still shown: cosmetic).",
    21: "After restart: 17 holdings, 10 picks, cached thesis all present.",
    22: "390 px wide, no horizontal page scroll; buy rows wrap cleanly.",
    23: "Dark theme readable: plan, sells (red), watch notes (amber).",
    24: "No screenshot; see output.",
    25: "OKLO row has a red 8-K tag; card Watch line 'SEC 8-K: material agreement terminated (2026-09-11)'.",
    26: "No screenshot; independent recomputation matches the app exactly.",
    27: "Guide tab renders USER_GUIDE.md in app style; honest table with the hindsight-free row (27% / 44% / 30%) and 4 clean bullets; App tab returns.",
    28: "Empty state live (inception 2026-09-30). Synthetic-fixture table (clearly labelled): rule/EW/SMH, 2 months × $2,500, shown as total return (<12 months) — no misleading annualization.",
    29: "'Since 2026-09-20: 2 new uptrends (ACLS, ACMR) · 1 lost uptrend (AAOI)' matches the edited snapshot.",
    30: "Both lab notes read from JSON: hand-picked OOS 40.0/36.9/39.9 and the bold hindsight-free line 26.6/44.2/29.7 with the ADR-009 sentence.",
}


def _pytest() -> str:
    out = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip().splitlines()
    return out[-1] if out else "?"


def main() -> None:
    res = json.loads((PROOF / "results.json").read_text())
    passed = sum(r["pass"] for r in res)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    fid = json.loads((ROOT / "lab/results/fidelity.json").read_text())["ai_OOS"]
    oos = {r["name"]: r for r in json.loads((ROOT / "lab/results/oos.json").read_text())["rows"]}
    pit = {r["name"]: r["xirr"] for r in json.loads((ROOT / "lab/results/h1.json").read_text())["ai_pit"]["rows"]}
    md = [f"# FinRes: proof that everything works ({date.today()}, commit `{commit}`)", "",
          "Every user flow was driven **live**: real market data, real SEC data, real local Qwen 3.8. Each ran against "
          "a copy of the database, was asserted automatically, and then **every screenshot was checked by eye** by "
          "the tech lead (right-hand column).", "",
          "| Check | Result |", "|---|---|",
          f"| Live user flows (`python -m e2e.flows`) | **{passed}/{len(res)} passed** |",
          f"| Unit/integration tests (`pytest -q`) | {_pytest()} |",
          "| Lab↔app parity (identical composites and buys at 2 dates) | "
          + ("pass" if next(r for r in res if r["id"] == 24)["pass"] else "FAIL") + " |",
          "| Independent data check (NVDA, VST, TSM vs yfinance recomputation) | "
          + ("pass" if next(r for r in res if r["id"] == 26)["pass"] else "FAIL") + " |",
          f"| Lab, out-of-sample 2019–26 (XIRR) | rule {fid[1]['xirr']:.1%} · EW universe {fid[2]['xirr']:.1%} · "
          f"SMH DCA {oos['DCA SMH']['xirr']:.1%} (hindsight-biased universe) |",
          f"| Lab, hindsight-free re-test 2010–26 (XIRR, ADR-009a) | rule {pit['rule (ew_trend10)']:.1%} · "
          f"EW hold {pit['EW (ew_all)']:.1%} · SMH DCA {pit['DCA SMH']:.1%} |", "",
          "| # | Flow | Expected | Auto | TL by-eye check | Screenshot |", "|---|---|---|---|---|---|"]
    for r in res:
        shots = r.get("screenshot") or []
        shots = [shots] if isinstance(shots, str) else shots
        links = " ".join(f"[{s}]({s})" for s in shots if s and s.endswith((".png", ".json")))
        syn = " **(28b: synthetic prices)**" if r.get("synthetic_prices") else ""
        md.append(f"| {r['id']} | {r['name']}{syn} | {r['expected']} | {'✅' if r['pass'] else '❌'} | "
                  f"{TL_CHECK.get(r['id'], '')} | {links or '–'} |")
    for rid in (24, 26, 28):
        r = next((x for x in res if x["id"] == rid), None)
        if r is None:
            continue
        md += ["", f"### Flow {rid} output ({r['name']})", "```", r["actual"].replace(" | ", "\n"), "```"]
    md += ["", "### User-guide dry run (2026-09-27, by the tech lead)",
           "Followed USER_GUIDE.md word for word on a fresh `git clone` into /tmp with a new Python 3.12 venv. "
           "`pip install -r requirements.txt` installed cleanly. `pytest -q` gave 95 passed. `./run.sh` served the "
           "page, the first visit auto-refreshed, and the page then showed \"Buy with $2,500 — 10 uptrend names × "
           "$250\". Analyze NVDA rendered. The server log had zero errors or tracebacks."]
    md += ["", "Details for each flow (steps and every assertion) are in [results.json](results.json). "
           "To reproduce, see [e2e/README.md](../../e2e/README.md)."]
    (PROOF / "PROOF.md").write_text("\n".join(md) + "\n")
    print(f"wrote {PROOF / 'PROOF.md'} ({passed}/{len(res)} flows)")


if __name__ == "__main__":
    main()
