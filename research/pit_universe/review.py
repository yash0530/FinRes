"""ADR-010 D, the yearly universe review. CLI: python -m research.pit_universe.review --year 2026 [--extract]
Suggests new universe.toml names (never edits it): latest 10-K filed in --year is R-A keyword-eligible, Qwen sells_into,
PIT TTM revenue >= $100M, and a current SEC ticker not in any universe.toml category. Also lists members Qwen says don't."""
import argparse, gzip, json  # noqa: E401
from datetime import date

from finres import config, edgar
from research.pit_universe import classify as c
from research.pit_universe.build import load_facts
from research.pit_universe.scan import DATA, GROUPS, HERE, UTILITY_SIC, guard, read_csv

ROLE_CAT = {"compute": "compute", "semis_equipment": "equipment", "memory_storage": "memory",
            "networking": "networking", "datacenter_infra": "datacenter", "ai_software": "software"}
FLOOR, HYPER = 100e6, 50e9


def category(role: str, sic, revenue: float) -> str:
    if role == "power_energy":
        return "power" if str(sic).startswith("49") else "grid"
    if role == "cloud":
        return "hyperscalers" if revenue >= HYPER else "software"
    return ROLE_CAT.get(role, "—")


def ra_score(r: dict) -> float:
    """build.eligible's R-A score (rule (2, GROUPS)) per 10k words: utility/uranium SICs count power hits only."""
    gs = ["power"] if r["in_group"] == "1" and int(r["sic_filing"]) in UTILITY_SIC else GROUPS  # out-of-group: no hits
    return 1e4 * sum(int(r[f"hits_{g}"]) for g in gs) / max(int(r["words"]), 1) if r["in_group"] == "1" else 0.0


def latest(scores: list[dict], year: int) -> dict[str, dict]:
    """{cik: its latest (filed, acc) 10-K filed in `year`}."""
    return {r["cik"]: r for r in sorted((r for r in scores if r["filed"][:4] == str(year)), key=lambda r: (r["filed"], r["acc"]))}


def tickers(co: dict) -> list[str]:
    return [t.strip().upper().replace("-", ".") for t in (co.get("tickers") or "").split("|") if t.strip()]


def review(filings: dict, cls: dict, companies: dict, members: dict, revenue) -> tuple[list, list, list]:
    """(candidates by revenue desc, members Qwen classifies sells_into = false, candidates awaiting classification)."""
    cands, removals, pending = [], [], []
    for cik, f in filings.items():
        ts, q = tickers(companies.get(cik, {})), cls.get(f["acc"])
        mem = [t for t in ts if t in members]
        r = {"cik": cik, "acc": f["acc"], "filed": f["filed"], "ticker": (mem or ts or [""])[0], "sic": f["sic_filing"],
             "name": companies.get(cik, {}).get("name", ""), "score": ra_score(f), "role": (q or {}).get("role", ""),
             "evidence": (q or {}).get("evidence") or "—"}
        if mem:
            removals += [r | {"category": members[mem[0]]}] if q and str(q["sells_into"]) == "False" else []
        elif ts and r["score"] >= 2 and (rev := revenue(cik) or 0) >= FLOOR:
            r |= {"revenue": rev, "category": category(r["role"], r["sic"], rev)}
            pending += [r] if q is None else []
            cands += [r] if q and str(q["sells_into"]) == "True" else []
    return sorted(cands, key=lambda r: -r["revenue"]), removals, pending


def revenue(cik: str, asof: date) -> float | None:
    """PIT TTM revenue at `asof` from SEC companyfacts (fetched into data/facts/ if not cached)."""
    ok, err = guard(load_facts, int(cik))
    p = DATA / "facts" / f"{cik}.json.gz"
    return None if err or ok != "ok" else edgar.fundamentals(json.loads(gzip.decompress(p.read_bytes())), asof)["revenue_ttm"]


def markdown(year: int, cands: list, removals: list, pending: list, partial: bool) -> str:
    md = [f"# Universe review {year} (ADR-010 D)", "", f"Generated {date.today()}. Latest 10-K filed in {year}: R-A keyword-eligible "
          "(≥ 2 per 10k words), Qwen `sells_into`, PIT TTM revenue ≥ $100M, current SEC ticker in no universe.toml category. "
          "Suggestions only: edit universe.toml by hand.", ""]
    md += ["**PARTIAL: the classification run has not finished.**", ""] if partial else []
    md += [f"**{len(pending)} filings pass the other conditions but are not classified yet:** "
           + " ".join(r["ticker"] for r in pending) + f". Run `review --year {year} --extract`, then `classify run`.", ""] if pending else []
    md += [f"## Candidates ({len(cands)})", "| ticker | name | SIC | Qwen role | suggested category | revenue TTM | evidence | R-A /10k |",
           "|---|---|---|---|---|---|---|---|", *[f"| {r['ticker']} | {r['name']} | {r['sic']} | {r['role']} | {r['category']} | "
           f"${r['revenue'] / 1e9:.2f}B | {r['evidence'].replace('|', '/')} | {r['score']:.1f} |" for r in cands], ""]
    return "\n".join(md + [f"## Members to review for removal ({len(removals)}; informational)",
                           "| ticker | category | name | 10-K filed | evidence |", "|---|---|---|---|---|",
                           *[f"| {r['ticker']} | {r['category']} | {r['name']} | {r['filed']} | {r['evidence'].replace('|', '/')} |"
                             for r in removals], ""])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m research.pit_universe.review")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--extract", action="store_true", help="add the unclassified filings to business.csv for `classify run`")
    a = ap.parse_args(argv)
    filings, members = latest(read_csv(DATA / "scores.csv"), a.year), config.load_universe()["ticker_category"]
    cls, companies = {r["acc"]: r for r in read_csv(DATA / "qwen_class.csv")}, {r["cik"]: r for r in read_csv(DATA / "companies.csv")}
    asof = min(date.today(), date(a.year, 12, 31))
    cands, removals, pending = review(filings, cls, companies, members, lambda k: revenue(k, asof))
    if a.extract:
        mem = [f for k, f in filings.items() if f["acc"] not in cls and set(tickers(companies.get(k, {}))) & set(members)]
        c.step_extract([{k: f[k] for k in ("acc", "cik", "filed")} for f in pending + mem])
        return print("now: python -m research.pit_universe.classify run, then this review again without --extract")
    (out := HERE / f"review_{a.year}.md").write_text(markdown(a.year, cands, removals, pending, not (DATA / "classify.done").exists()))
    print(f"wrote {out}: {len(cands)} candidates, {len(removals)} removal reviews, {len(pending)} unclassified")


if __name__ == "__main__":
    main()
