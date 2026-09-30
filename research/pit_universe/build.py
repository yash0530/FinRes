"""ADR-007 PIT universe build. CLI: python -m research.pit_universe.build {universe|map|facts|prices|report}"""
import argparse
import csv
import gzip
import io
import os
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
import pandas as pd

from finres import db, prices
from research.pit_universe.scan import DATA, HERE, WORKERS, fetch, guard, log_error, read_csv, write_csv

YEARS = range(2010, 2027)
EXCHANGES = {"NYSE", "NASDAQ", "NYSE MKT", "AMEX", "NYSE ARCA"}
TIINGO_LIST = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
TIINGO_PX = "https://api.tiingo.com/tiingo/daily/{}/prices"
PER_HOUR, PER_DAY, LISTED_FROM = 50, 1000, "2026-09-01"
LAB_DB = HERE.parents[1] / "lab" / "data" / "lab.db"
ELIG_COLS = ["year", "cik", "name", "sic", "symbols_from_filing", "filed"]
UNI_COLS = ["year", "cik", "name", "sic", "group", "ticker", "tiingo_start", "tiingo_end", "exchange", "status",
            "filed", "last_10k"]


# ---------- step 1: universe ----------

def eligible(scores: list[dict], names: dict | None = None) -> list[dict]:
    """Company-years: year Y uses the CIK's LATEST 10-K filed in Y-1 (by filing date); kept iff in_group & eligible_text."""
    latest: dict = {}
    for r in scores:
        k = (int(r["filed"][:4]) + 1, int(r["cik"]))
        if k not in latest or (r["filed"], r["acc"]) > (latest[k]["filed"], latest[k]["acc"]):
            latest[k] = r
    return [{"year": y, "cik": c, "name": (names or {}).get(str(c), ""), "sic": r["sic_filing"],
             "symbols_from_filing": r.get("symbols") or "", "filed": r["filed"]}
            for (y, c), r in sorted(latest.items())
            if y in YEARS and r.get("in_group") == "1" and r.get("eligible_text") == "1"]


def step_universe() -> None:
    names = {r["cik"]: r["company"] for r in read_csv(DATA / "tenk_index.csv")}
    names |= {r["cik"]: r["name"] for r in read_csv(DATA / "companies.csv") if r["name"]}
    rows = eligible(read_csv(DATA / "scores.csv"), names)
    write_csv(DATA / "eligible.csv", rows, ELIG_COLS)
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


def pick_row(cands: list[str], tiingo: dict, year: int, span: tuple[str, str]):
    """(index, row) of the first candidate with a row overlapping `year`; recycled ticker -> best Jaccard vs filings."""
    def fit(r):
        o = overlap(r["startDate"], r["endDate"], *span)
        return o / overlap(min(r["startDate"], span[0]), max(r["endDate"], span[1]), "0000", "9999")
    for i, t in enumerate(cands):
        ok = [r for r in tiingo.get(t, []) if overlap(r["startDate"], r["endDate"], f"{year}-01-01", f"{year}-12-31")]
        if ok:
            return i, max(ok, key=lambda r: (fit(r), r["endDate"]))
    return None, None


def norm(s: str) -> list[str]:
    return [x.strip().upper().replace(".", "-") for x in (s or "").split("|") if x.strip()]


def map_rows(elig: list[dict], scores: list[dict], sub_tickers: dict, tiingo: dict) -> tuple[list[dict], list[dict]]:
    """(universe rows, review list). One CIK per (year, Tiingo row): the ticker's owner in submissions wins."""
    filings = defaultdict(list)
    for r in scores:
        filings[str(r["cik"])].append(r)
    best, review = {}, []
    for e in elig:
        cik, y, fs = str(e["cik"]), int(e["year"]), filings[str(e["cik"])]
        others = [s for f in sorted(fs, key=lambda f: (abs(int(f["filed"][:4]) - y + 1), f["filed"]))
                  for s in norm(f.get("symbols"))]
        own = norm(sub_tickers.get(cik, ""))
        cands = list(dict.fromkeys(norm(e["symbols_from_filing"]) + others + own))
        cands += [c + "Q" for c in cands]  # Tiingo renames bankrupt names (INAP -> INAPQ)
        last = max((f["filed"] for f in fs), default=e["filed"])
        span = (min((f["filed"] for f in fs), default=e["filed"]), (date.fromisoformat(last) + timedelta(365)).isoformat())
        i, row = pick_row(cands, tiingo, y, span)
        u = {**{k: e[k] for k in ("year", "name", "sic", "filed")}, "cik": cik, "group": group_of(e["sic"]),
             "last_10k": last, "ticker": "", "tiingo_start": "", "tiingo_end": "", "exchange": "", "status": "unmapped"}
        if row is None:
            review.append({**u, "reason": "unmapped", "candidates": "|".join(cands)})
            best[(y, "?" + cik)] = [(0, u)]
            continue
        u |= {"ticker": row["ticker"].upper(), "tiingo_start": row["startDate"], "tiingo_end": row["endDate"],
              "exchange": row["exchange"], "status": "listed" if row["endDate"] >= LISTED_FROM else "delisted"}
        best.setdefault((y, u["ticker"], row["startDate"]), []).append(((u["ticker"] not in own, i, int(cik)), u))
    keep = []
    for lst in best.values():
        lst.sort(key=lambda p: p[0])
        keep.append(lst[0][1])
        review += [{**u, "reason": "duplicate ticker", "candidates": ""} for _, u in lst[1:]]
    return sorted(keep, key=lambda u: (int(u["year"]), int(u["cik"]))), review


def step_map() -> None:
    sub = {r["cik"]: r["tickers"] for r in read_csv(DATA / "companies.csv")}
    rows, review = map_rows(read_csv(DATA / "eligible.csv"), read_csv(DATA / "scores.csv"), sub, load_tiingo())
    write_csv(HERE / "universe.csv", rows, UNI_COLS)
    write_csv(DATA / "unmapped.csv", review, UNI_COLS + ["reason", "candidates"])
    print(f"universe.csv: {len(rows)} company-years; review list: {Counter(r['reason'] for r in review)}")
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
    ciks = sorted({int(r["cik"]) for r in read_csv(HERE / "universe.csv")})
    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(lambda c: guard(load_facts, c), ciks))
    [log_error("facts", str(c), err) for c, (_, err) in zip(ciks, res) if err]
    print(f"facts: {len(ciks)} CIKs;", Counter(s or "error" for s, _ in res))


# ---------- step 4: prices ----------

def tiingo_fetch(todo: list[dict], key: str, store, get=httpx.get, sleep=time.sleep, clock=time.time) -> int:
    """Prioritized delisted prices (adjClose), <=50/h & <=1,000/day, resumable; returns #left (monthly quota stop)."""
    path = DATA / "tiingo_done.csv"
    done = read_csv(path)
    seen, stamps = {r["cik"] for r in done}, [float(r["at"]) for r in done]
    queue = [u for u in todo if str(u["cik"]) not in seen]
    i = 0
    while i < len(queue):
        u, now = queue[i], clock()
        hour, day = [s for s in stamps if now - s < 3600], [s for s in stamps if now - s < 86400]
        if len(hour) >= PER_HOUR or len(day) >= PER_DAY:
            sleep((min(hour) + 3600 if len(hour) >= PER_HOUR else min(day) + 86400) - now + 1)
            continue
        r = get(TIINGO_PX.format(u["ticker"]), params={"startDate": u["start"], "endDate": u["end"], "token": key},
                timeout=60)
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
    todo = []
    for c, rs in by.items():
        d = [r for r in rs if r["status"] == "delisted"]
        if c not in listed and d:
            t = Counter(r["ticker"] for r in d).most_common(1)[0][0]
            r = next(r for r in d if r["ticker"] == t)
            todo.append({"cik": c, "ticker": t, "start": r["tiingo_start"], "end": r["tiingo_end"], "years": len(rs)})
    return listed, sorted(todo, key=lambda u: (-u["years"], int(u["cik"])))


def step_prices() -> None:
    conn = db.connect(LAB_DB)
    listed, todo = price_plan(read_csv(HERE / "universe.csv"))
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
    write_csv(DATA / "unpriced.csv", [u for u in todo if u["cik"] not in spans], list(todo[0]) if todo else [])
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
    md = coverage(read_csv(HERE / "universe.csv"), read_csv(DATA / "unmapped.csv"), priced_spans(db.connect(LAB_DB)))
    (HERE / "coverage.md").write_text(md)
    print(md)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m research.pit_universe.build")
    ap.add_argument("step", choices=["universe", "map", "facts", "prices", "report"])
    a = ap.parse_args(argv)
    {"universe": step_universe, "map": step_map, "facts": step_facts, "prices": step_prices, "report": step_report}[a.step]()


if __name__ == "__main__":
    main()
