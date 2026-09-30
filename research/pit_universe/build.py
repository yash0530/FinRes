"""ADR-007 PIT universe build. CLI: python -m research.pit_universe.build {universe|map|facts|prices|report}"""
import argparse, csv, gzip, io, os, time, zipfile  # noqa: E401
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pandas as pd

from finres import db, prices
from research.pit_universe.scan import DATA, GROUPS, HERE, NOT_SYM, UTILITY_SIC, WORKERS, fetch, guard, log_error, read_csv, write_csv

YEARS = range(2010, 2027)
EXCHANGES = {"NYSE", "NASDAQ", "NYSE MKT", "AMEX", "NYSE ARCA"}
TIINGO_LIST = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
TIINGO_PX = "https://api.tiingo.com/tiingo/daily/{}/prices"
PER_HOUR, PER_DAY, LISTED_FROM = 50, 1000, "2026-09-01"
LAB_DB = HERE.parents[1] / "lab" / "data" / "lab.db"
ELIG_COLS = ["year", "cik", "name", "sic", "symbols_from_filing", "filed"]
UNI_COLS = ["year", "cik", "name", "sic", "group", "ticker", "tiingo_start", "tiingo_end", "exchange", "status",
            "filed", "last_10k"]
TAG = ""  # ADR-007a robustness universes: "_ra" / "_rb" suffix on every output; "" = the frozen ADR-007 universe


# ---------- step 1: universe ----------

def eligible(scores: list[dict], names: dict | None = None, rule: tuple | None = None) -> list[dict]:
    """Company-years: year Y uses the CIK's LATEST 10-K filed in Y-1 (by filing date); kept iff in_group & eligible_text."""
    # rule=(threshold, groups) re-derives eligible_text from the stored hits (ADR-007a robustness universes);
    # utility/uranium SICs keep the power/10k >= 2 rule while "power" is one of the groups.
    per = lambda r, gs: 1e4 * sum(int(r[f"hits_{g}"]) for g in gs) / max(int(r["words"]), 1)  # noqa: E731
    ok = lambda r: r.get("eligible_text") == "1" if rule is None else per(r, ["power"]) >= 2 if int(  # noqa: E731
        r["sic_filing"]) in UTILITY_SIC and "power" in rule[1] else per(r, rule[1]) >= rule[0]
    latest: dict = {}
    for r in scores:
        k = (int(r["filed"][:4]) + 1, int(r["cik"]))
        if k not in latest or (r["filed"], r["acc"]) > (latest[k]["filed"], latest[k]["acc"]):
            latest[k] = r
    return [{"year": y, "cik": c, "name": (names or {}).get(str(c), ""), "sic": r["sic_filing"],
             "symbols_from_filing": r.get("symbols") or "", "filed": r["filed"]}
            for (y, c), r in sorted(latest.items())
            if y in YEARS and r.get("in_group") == "1" and ok(r)]


def step_universe(rule=None) -> None:
    names = {r["cik"]: r["company"] for r in read_csv(DATA / "tenk_index.csv")}
    names |= {r["cik"]: r["name"] for r in read_csv(DATA / "companies.csv") if r["name"]}
    rows = eligible(read_csv(DATA / "scores.csv"), names, rule)
    write_csv(DATA / f"eligible{TAG}.csv", rows, ELIG_COLS)
    print(f"{len(rows)} eligible company-years:", dict(sorted(Counter(r["year"] for r in rows).items())))


# ---------- step 2: map ----------

def group_of(sic) -> str:
    s = int(sic) if str(sic).isdigit() else -1
    if 3670 <= s <= 3679 or (3570 <= s <= 3579 and s != 3576):
        return "semis"
    if s == 3576 or 3661 <= s <= 3669:
        return "network"
    if 7370 <= s <= 7379:
        return "cloud"
    return "infra" if 3612 <= s <= 3629 or s in {3585, 3812, 4911, 4931, 4991, 6798, 1094} else "other"


def load_tiingo(text: str | None = None) -> dict[str, list[dict]]:
    """Tiingo supported_tickers rows (US stocks on major exchanges, with dates) by ticker; the zip is cached."""
    if text is None:
        path = DATA / "supported_tickers.zip"
        if not path.exists():
            path.write_bytes(httpx.get(TIINGO_LIST, timeout=120, follow_redirects=True).raise_for_status().content)
        with zipfile.ZipFile(path) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    out = defaultdict(list)
    for r in csv.DictReader(io.StringIO(text)):
        if r["exchange"] in EXCHANGES and (r["assetType"], r["priceCurrency"]) == ("Stock", "USD") and r["endDate"]:
            out[r["ticker"].upper()].append(r)
    return out


def overlap(a0: str, a1: str, b0: str, b1: str) -> int:
    lo, hi = max(a0, b0), min(a1, b1)
    return (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1 if lo <= hi else 0


def row_at(t: str, tiingo: dict, d0: str, d1: str) -> dict | None:
    """t's latest-ending Tiingo row that overlaps d0..d1."""
    return max((r for r in tiingo.get(t, []) if overlap(r["startDate"], r["endDate"], d0, d1)), key=lambda r: r["endDate"], default=None)


def norm(s: str, drop=frozenset()) -> list[str]:
    return [x for x in (x.strip().upper().replace(".", "-") for x in (s or "").split("|")) if x and x not in drop]


def map_rows(elig: list[dict], scores: list[dict], sub_tickers: dict, tiingo: dict) -> tuple[list[dict], list[dict]]:
    """(universe rows, review list). M10/F2: "listed" ONLY via the CIK's current SEC ticker (submissions) with an active
    Tiingo row started by year end; else "delisted" via a filing-stated symbol (this year's filing first, then the
    nearest) whose row covers THAT filing's date and is alive after this year's filing (never Yahoo); else "unmapped".
    One CIK per (year, Tiingo row): the ticker's owner in submissions wins."""
    filings = defaultdict(list)
    for r in scores:
        filings[str(r["cik"])].append(r)
    best, review = {}, []
    for e in elig:
        cik, y, fs = str(e["cik"]), int(e["year"]), filings[str(e["cik"])]
        own, end = norm(sub_tickers.get(cik, "")), f"{y}-12-31"
        own = sorted(own, key=lambda t: any(t[len(o):] in ("W", "WS", "U", "R") and t.startswith(o) for o in own))  # AIRJ<AIRJW
        near = [(e["filed"], e["symbols_from_filing"])] + [(f["filed"], f.get("symbols")) for f in sorted(
            fs, key=lambda f: (abs(int(f["filed"][:4]) - y + 1), f["filed"]))]
        cands = [(t, r) for t in own if (r := row_at(t, tiingo, LISTED_FROM, "9999")) and r["startDate"] <= end]
        cands += [(t, r) for d, s in near for x in norm(s, NOT_SYM) for t in (x, x + "Q")  # INAP -> INAPQ (bankrupt)
                  if (r := row_at(t, tiingo, d, d)) and overlap(r["startDate"], r["endDate"], e["filed"], end)]
        last = max((f["filed"] for f in fs), default=e["filed"])
        u = {**{k: e[k] for k in ("year", "name", "sic", "filed")}, "cik": cik, "group": group_of(e["sic"]),
             "last_10k": last, "ticker": "", "tiingo_start": "", "tiingo_end": "", "exchange": "", "status": "unmapped"}
        if not cands:
            review.append({**u, "reason": "unmapped", "candidates": "|".join(own + [x for _, s in near for x in norm(s)])})
            best[(y, "?" + cik)] = [(0, u)]
            continue
        t, row = cands[0]
        u |= {"ticker": t, "tiingo_start": row["startDate"], "tiingo_end": row["endDate"], "exchange": row["exchange"],
              "status": "listed" if t in own and row["endDate"] >= LISTED_FROM else "delisted"}
        best.setdefault((y, t, row["startDate"]), []).append(((t not in own, 0, int(cik)), u))
    ranked = [[u for _, u in sorted(lst, key=lambda p: p[0])] for lst in best.values()]
    keep, review = [us[0] for us in ranked], review + [{**u, "reason": "duplicate ticker", "candidates": ""} for us in ranked for u in us[1:]]
    return sorted(keep, key=lambda u: (int(u["year"]), int(u["cik"]))), review


def cik_status(rows: list[dict]) -> dict[str, tuple]:
    """{cik: (status, ticker, tiingo_start)}: listed if any year is listed, else delisted if any, else unmapped."""
    order = lambda r: ["unmapped", "delisted", "listed"].index(r["status"])  # noqa: E731 - the last write wins
    return {r["cik"]: (r["status"], r["ticker"], r["tiingo_start"]) for r in sorted(rows, key=order)}


def step_map() -> None:
    sub = {r["cik"]: r["tickers"] for r in read_csv(DATA / "companies.csv")}
    old = cik_status(read_csv(HERE / f"universe{TAG}.csv"))
    rows, review = map_rows(read_csv(DATA / f"eligible{TAG}.csv"), read_csv(DATA / "scores.csv"), sub, load_tiingo())
    write_csv(HERE / f"universe{TAG}.csv", rows, UNI_COLS)
    write_csv(DATA / f"unmapped{TAG}.csv", review, UNI_COLS + ["reason", "candidates"])
    print(f"universe{TAG}.csv: {len(rows)} company-years; review list: {Counter(r['reason'] for r in review)}")
    new, names = cik_status(rows), {r["cik"]: r["name"] for r in rows}
    diff = [(c, old.get(c), s) for c, s in sorted(new.items(), key=lambda p: int(p[0])) if old.get(c) != s]
    stale = [(f"C{c}",) for c, a, _ in diff if a and a[0] == "listed"]  # its Yahoo series came from another ticker
    print(f"status diff, {len(diff)} CIKs (status, ticker, row start):", *(f"  C{c} {names[c]}: {a} -> {b}" for c, a, b in diff), sep="\n")
    with db.connect(LAB_DB) as conn:
        conn.executemany("DELETE FROM prices WHERE ticker = ?", stale)
    print(f"deleted {len(stale)} stale C{{cik}} price series from lab.db:", " ".join(t for (t,) in stale))
    step_report()


# ---------- step 3: facts ----------

def load_facts(cik: int) -> str:
    path, miss = DATA / "facts" / f"{cik}.json.gz", DATA / "facts" / f"{cik}.404"
    if not path.exists() and not miss.exists():
        r = fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        miss.touch() if r.status_code == 404 else path.write_bytes(gzip.compress(r.raise_for_status().content))
    return "ok" if path.exists() else "404"


def step_facts() -> None:
    """companyfacts for every eligible CIK (mapped or not: the stress test needs the revenue floor for both)."""
    (DATA / "facts").mkdir(parents=True, exist_ok=True)
    ciks = sorted({int(r["cik"]) for r in read_csv(HERE / f"universe{TAG}.csv")})
    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(lambda c: guard(load_facts, c), ciks))
    [log_error("facts", str(c), err) for c, (_, err) in zip(ciks, res) if err]
    print(f"facts: {len(ciks)} CIKs;", Counter(s or "error" for s, _ in res))


# ---------- step 4: prices ----------

def tiingo_fetch(todo: list[dict], key: str, store, get=httpx.get, sleep=time.sleep, clock=time.time) -> int:
    """Prioritized delisted prices (adjClose), <=50/h & <=1,000/day, resumable; returns #left (monthly quota stop)."""
    done = read_csv(path := DATA / "tiingo_done.csv")
    seen, stamps = {r["cik"] for r in done}, [float(r["at"]) for r in done]
    queue, i = [u for u in todo if str(u["cik"]) not in seen], 0
    while i < len(queue):
        u, now = queue[i], clock()
        hour, day = [s for s in stamps if now - s < 3600], [s for s in stamps if now - s < 86400]
        if len(hour) >= PER_HOUR or len(day) >= PER_DAY:
            sleep((min(hour) + 3600 if len(hour) >= PER_HOUR else min(day) + 86400) - now + 1)
            continue
        r = get(TIINGO_PX.format(u["ticker"]), params={"startDate": u["start"], "endDate": u["end"], "token": key}, timeout=60)
        stamps.append(clock())
        text = r.text.lower() if r.status_code != 200 else ""
        if "month" in text:
            print(f"tiingo: monthly quota reached; {len(queue) - i} delisted names remain unpriced")
            return len(queue) - i
        if r.status_code == 429 or "hour" in text or "day" in text:
            sleep(3600)
            continue
        px = {p["date"][:10]: p["adjClose"] for p in r.json()} if r.status_code == 200 else {}
        store(u["cik"], pd.Series(px, dtype=float).rename(index=pd.Timestamp)) if px else None
        done.append({"cik": u["cik"], "ticker": u["ticker"], "status": r.status_code, "rows": len(px), "at": stamps[-1]})
        write_csv(path, done, list(done[-1]))  # after store: a crash never marks an unstored name done
        i += 1
    return 0


def price_plan(rows: list[dict]) -> tuple[dict, list[dict]]:
    """({cik: listed row}, delisted todo sorted by eligible years desc). A CIK with any listed year is priced by Yahoo."""
    by = defaultdict(list)
    for r in rows:
        by[r["cik"]].append(r)
    listed = {c: max((r for r in rs if r["status"] == "listed"), key=lambda r: int(r["year"]))
              for c, rs in by.items() if any(r["status"] == "listed" for r in rs)}
    todo = [{"cik": c, "ticker": t, "start": r["tiingo_start"], "end": r["tiingo_end"], "years": len(rs)}
            for c, rs in by.items() if c not in listed and (d := [r for r in rs if r["status"] == "delisted"])
            for t in [Counter(r["ticker"] for r in d).most_common(1)[0][0]] for r in [next(r for r in d if r["ticker"] == t)]]
    return listed, sorted(todo, key=lambda u: (-u["years"], int(u["cik"])))


def step_prices() -> None:
    conn = db.connect(LAB_DB)
    base = {r["cik"] for r in read_csv(HERE / "universe.csv")} if TAG else set()  # robustness: only NEW CIKs
    listed, todo = price_plan([r for r in read_csv(HERE / f"universe{TAG}.csv") if r["cik"] not in base])
    listed = {c: r for c, r in listed.items() if c not in priced_spans(conn)}  # M10: only new/changed (map deletes)
    bench = [b for b in ("SPY", "QQQ", "SMH") if not conn.execute("SELECT 1 FROM prices WHERE ticker=?", (b,)).fetchone()]
    closes, failed = prices.download(sorted({r["ticker"] for r in listed.values()}) + bench, period="max")
    prices.store(conn, closes[[b for b in bench if b in closes]])
    out = {f"C{c}": closes[r["ticker"]].loc[r["tiingo_start"]:]  # dates >= tiingo_start guard recycled tickers
           for c, r in listed.items() if r["ticker"] in closes}
    prices.store(conn, pd.DataFrame(out))
    print(f"yahoo: {len(out)}/{len(listed)} listed CIKs priced; failed: {failed}")
    store = lambda c, s: prices.store(conn, pd.DataFrame({f"C{c}": s}))  # noqa: E731
    key = os.environ.get("TIINGO_API_KEY")
    left = tiingo_fetch(todo, key, store) if key else len(todo)
    spans = priced_spans(conn)
    write_csv(DATA / f"unpriced{TAG}.csv", [u for u in todo if u["cik"] not in spans], list(todo[0]) if todo else [])
    print(f"tiingo: {'no TIINGO_API_KEY, skipped' if not key else 'done'}; {left} of {len(todo)} delisted CIKs unpriced")
    step_report()


# ---------- step 5: report ----------

def priced_spans(conn) -> dict[str, tuple[str, str]]:
    q = "SELECT ticker, MIN(d), MAX(d) FROM prices WHERE ticker LIKE 'C%' GROUP BY ticker"
    return {r[0][1:]: (r[1], r[2]) for r in conn.execute(q) if r[0][1:].isdigit()}


def coverage(rows: list[dict], review: list[dict], spans: dict) -> str:
    lines = ["# PIT universe coverage (ADR-007)", "",
             "`priced` = prices as `C{cik}` in lab/data/lab.db inside the year; unpriced company-years enter only the "
             "delisting stress test. Duplicate CIKs on one ticker are dropped. The $100M floor is applied per month.", "",
             "| year | eligible | mapped | listed | delisted | unmapped | dup dropped | priced | unpriced | priced share |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    priced = lambda r: r["cik"] in spans and overlap(*spans[r["cik"]], f"{r['year']}-01-01", f"{r['year']}-12-31") > 0
    for y in YEARS:
        rs = [r for r in rows if int(r["year"]) == y]
        st, p = Counter(r["status"] for r in rs), sum(map(priced, rs))
        dup = sum(int(r["year"]) == y and r["reason"] == "duplicate ticker" for r in review)
        lines.append(f"| {y} | {len(rs) + dup} | {len(rs) - st['unmapped'] + dup} | {st['listed']} | {st['delisted']} "
                     f"| {st['unmapped']} | {dup} | {p} | {len(rs) - p} | {p / len(rs) if rs else 0:.0%} |")
    n, p = len(rows), sum(map(priced, rows))
    return "\n".join(lines + [f"| all (after dups) | {n} | | | | | | {p} | {n - p} | {p / n if n else 0:.0%} |", ""])


def step_report() -> None:
    md = coverage(read_csv(HERE / f"universe{TAG}.csv"), read_csv(DATA / f"unmapped{TAG}.csv"), priced_spans(db.connect(LAB_DB)))
    (HERE / f"coverage{TAG}.md").write_text(md)
    print(md)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m research.pit_universe.build")
    ap.add_argument("step", choices=["universe", "map", "facts", "prices", "report"])
    # ADR-007a: universe --threshold 2 --tag ra (R-A); universe --groups compute,network --tag rb (R-B); then map,
    # facts, prices with the same --tag (outputs get a _ra/_rb suffix; prices only for CIKs not in universe.csv).
    [ap.add_argument(f) for f in ("--threshold", "--groups", "--tag")]
    a = ap.parse_args(argv)
    globals()["TAG"] = f"_{a.tag}" if a.tag else ""
    rule = (float(a.threshold or 5), a.groups.split(",") if a.groups else GROUPS) if a.threshold or a.groups else None
    {"universe": lambda: step_universe(rule), "map": step_map, "facts": step_facts, "prices": step_prices, "report": step_report}[a.step]()


if __name__ == "__main__":
    main()
