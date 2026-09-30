"""ADR-007 PIT 10-K scan. CLI: python -m research.pit_universe.scan {index|sic|score|summary} [--year Y] [--limit N]"""
import argparse
import csv
import gzip
import html
import json
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from finres import edgar

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DICT = json.loads((HERE / "dictionary.json").read_text())
GROUPS = ["compute", "ai", "network", "power", "cooling"]
UTILITY_SIC = {4911, 4931, 4991, 1094}
SIC_RANGES = [(3570, 3579), (3612, 3629), (3661, 3669), (3670, 3679), (3812, 3812), (3585, 3585),
              (7370, 7379), (6798, 6798)] + [(s, s) for s in UTILITY_SIC]
FORMS = {"10-K", "10-K405"}
FIRST, LAST = (2009, 1), (2026, 3)
SCORE_COLS = ["cik", "acc", "filed", "form", "sic_filing", "sic_src", "in_group", "words"] \
    + [f"hits_{g}" for g in GROUPS] + ["per10k_total", "per10k_power", "per10k_generic", "eligible_text", "symbols"]
RATE, WORKERS = 7, 8  # global req/s across worker threads (spec: <= 8/s); workers hide latency
_lock, _slot = threading.Lock(), [0.0]
IDX_RE = re.compile(r"^(\S+(?: \S+)*?)\s{2,}(.+?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$")


def in_group(sic) -> bool:
    s = int(sic) if str(sic).isdigit() else -1
    return any(lo <= s <= hi for lo, hi in SIC_RANGES)


def fetch(url: str):
    """edgar._get (SEC UA) behind a thread-safe global pacer, with backoff on 429/5xx."""
    for attempt in range(4):
        with _lock:  # reserve the next 1/RATE slot; edgar._get's own limiter adds spacing on top
            _slot[0] = max(_slot[0] + 1 / RATE, time.monotonic())
            wait = max(0.0, _slot[0] - time.monotonic())
        time.sleep(wait)
        r = edgar._get(url)
        if r.status_code not in (429, 500, 502, 503, 504):
            return r
        time.sleep(2 ** attempt * 5)
    return r  # callers raise_for_status


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)


def log_error(where: str, key: str, err) -> None:
    with open(DATA / "errors.csv", "a", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "key", "error", "at"]) if f.tell() == 0 else None
        w.writerow([where, key, str(err)[:300], time.strftime("%Y-%m-%dT%H:%M:%S")])


# ---------- step 1: index ----------

def parse_form_idx(text: str) -> list[dict]:
    """Rows of a full-index form.idx with Form Type exactly 10-K or 10-K405."""
    out = []
    for line in text.splitlines():
        m = IDX_RE.match(line)
        if not m or m.group(1) not in FORMS:
            continue
        path = m.group(5)
        out.append({"cik": int(m.group(3)), "company": m.group(2).strip(), "form": m.group(1),
                    "filed": m.group(4), "path": path, "acc": path.rsplit("/", 1)[-1].removesuffix(".txt")})
    return out


def step_index() -> None:
    (DATA / "idx").mkdir(parents=True, exist_ok=True)
    rows = []
    for y, q in [(y, q) for y in range(FIRST[0], LAST[0] + 1) for q in range(1, 5) if FIRST <= (y, q) <= LAST]:
        path = DATA / "idx" / f"{y}Q{q}.idx.gz"
        stale = (y, q) == LAST and path.exists() and time.time() - path.stat().st_mtime > 86400
        if not path.exists() or stale:
            r = fetch(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx")
            path.write_bytes(gzip.compress(r.raise_for_status().content))
        rows += parse_form_idx(gzip.decompress(path.read_bytes()).decode("latin-1"))
    rows = list({r["acc"]: r for r in rows}.values())
    write_csv(DATA / "tenk_index.csv", rows, ["cik", "company", "form", "filed", "path", "acc"])
    per_year = Counter(r["filed"][:4] for r in rows)
    print(f"{len(rows)} 10-K/10-K405 filings;", " ".join(f"{y}:{n}" for y, n in sorted(per_year.items())))


# ---------- step 2: sic ----------

def company_row(cik: int, sub: dict | None) -> dict:
    status, sub = ("ok", sub) if sub is not None else ("404", {})
    former = "|".join(f"{f.get('name')} ({(f.get('from') or '')[:10]}..{(f.get('to') or '')[:10]})"
                      for f in sub.get("formerNames") or [])
    return {"cik": cik, "name": sub.get("name") or "", "sic": sub.get("sic") or "",
            "tickers": "|".join(sub.get("tickers") or []), "exchanges": "|".join(e or "" for e in sub.get("exchanges") or []),
            "former_names": former, "status": status}


def guard(fn, *args) -> tuple:
    """(result, None) or (None, exception): per-item failures are logged by the caller and the run continues."""
    try:
        return fn(*args), None
    except Exception as e:  # noqa: BLE001
        return None, e


def load_sub(cik: int) -> dict:
    path, miss = DATA / "sub" / f"{cik}.json.gz", DATA / "sub" / f"{cik}.404"
    if not path.exists() and not miss.exists():
        r = fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        if r.status_code == 404:
            miss.touch()
        else:
            path.write_bytes(gzip.compress(r.raise_for_status().content))
    return company_row(cik, json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None)


def step_sic() -> None:
    (DATA / "sub").mkdir(parents=True, exist_ok=True)
    index = read_csv(DATA / "tenk_index.csv")
    ciks = sorted({int(r["cik"]) for r in index})
    companies, t0 = {}, time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (cik, (row, err)) in enumerate(zip(ciks, ex.map(lambda c: guard(load_sub, c), ciks)), 1):
            companies[cik] = row or company_row(cik, None) | {"status": "error"}  # unknown SIC stays a candidate
            log_error("sic", str(cik), err) if err else None
            if i % 500 == 0:
                print(f"sic {i}/{len(ciks)} ({i / (time.time() - t0):.1f}/s)", flush=True)
    write_csv(DATA / "companies.csv", list(companies.values()), list(company_row(0, {}).keys()))
    cand = [r for r in index if in_group(companies[int(r["cik"])]["sic"]) or not companies[int(r["cik"])]["sic"]]
    write_csv(DATA / "candidates.csv", cand, ["cik", "company", "form", "filed", "path", "acc"])
    print(f"{len(ciks)} CIKs; {sum(c['status'] != 'ok' for c in companies.values())} unknown; "
          f"{len(cand)}/{len(index)} filings are candidates")


# ---------- step 3: score ----------

def parse_index_page(page: str, cik: int) -> tuple[int | None, str | None]:
    """(SIC at filing time for this filer, primary document URL) from an EDGAR -index.htm page."""
    blocks = sorted(page.split('class="companyInfo"')[1:], key=lambda b: f"CIK={cik:010d}" not in b)  # filer first
    found = (re.search(r"SIC=(\d{3,4})", b) or re.search(r"SIC</acronym>:\s*(?:<[^>]+>\s*)*(\d{3,4})", b) for b in blocks)
    m = next((m for m in found if m), None)
    sic = int(m.group(1)) if m else None
    docs = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        tds = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        href = re.search(r'href="([^"]+)"', tr)
        if len(tds) < 5 or not href:
            continue
        url = "https://www.sec.gov" + href.group(1).replace("/ix?doc=", "")
        typ = re.sub(r"<[^>]+>", "", tds[3]).strip().upper()
        size = int(re.sub(r"\D", "", tds[4]) or 0)
        docs.append((typ, url, size))
    primary = next((u for t, u, _ in docs if t in FORMS), None)
    if primary is None:
        rest = [(s, u) for t, u, s in docs if not t.startswith("EX-") and re.search(r"\.(htm|html|txt)$", u, re.I)
                and not re.search(r"\d{10}-\d{2}-\d{6}\.txt$", u)]
        primary = max(rest)[1] if rest else None
    return sic, primary


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|ix:header|head)\b.*?</\1\s*>", " ", raw)
    raw = re.sub(r"(?s)<[^>]*>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


PATTERNS = {g: [re.compile(r"\b" + r"\s+".join(map(re.escape, t.split())) + r"\b", re.I) for t in DICT[g]]
            for g in GROUPS}


def count_hits(text: str) -> dict:
    return {g: sum(len(p.findall(text)) for p in PATTERNS[g]) for g in GROUPS}


def score_text(text: str, sic: int) -> dict:
    words, hits = len(text.split()), count_hits(text)
    per = lambda n: round(n * 10000 / words, 3) if words else 0.0  # noqa: E731
    row = {"words": words, **{f"hits_{g}": hits[g] for g in GROUPS},
           "per10k_total": per(sum(hits.values())), "per10k_power": per(hits["power"]),
           "per10k_generic": per(hits["compute"] + hits["network"])}
    ok = row["per10k_power"] >= 2 if sic in UTILITY_SIC else row["per10k_total"] >= 5
    return row | {"eligible_text": int(ok)}


SYM_RES = [re.compile(r"under the (?:ticker |trading )?symbols? [\"“'(]*([A-Z]{1,5}(?:[.-][A-Z])?)\b"),
           re.compile(r"\b([A-Z]{1,5}(?:[.-][A-Z])?)\s+(?:The\s+)?(?:Nasdaq|NASDAQ|New York Stock Exchange|NYSE)\b")]
NOT_SYM = {"A", "I", "THE", "NYSE", "LLC", "INC", "USA", "US", "NA", "N", "PAR", "CORP", "CO", "LP", "ADS", "ADR",
           # M10/F2: index / data-vendor / filing words ("CRSP Total Return Index" attached CRISPR's prices to Cray)
           "CRSP", "NASDAQ", "AMEX", "SP", "DJIA", "RUSSELL", "INDEX", "GAAP", "SEC", "CEO", "CFO", "IPO", "ETF", "NAV"}


def symbols(text: str) -> str:
    """Trading symbol(s) the filing states for itself (cover page / Item 5), for point-in-time ticker mapping."""
    found = [m.group(1) for rx in SYM_RES for m in rx.finditer(text[:400_000])]
    return "|".join(dict.fromkeys(x for x in found if x not in NOT_SYM))[:40]


def score_filing(f: dict, current_sic: str) -> dict:
    cik, acc = int(f["cik"]), f["acc"]
    r = fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm")
    sic, primary = parse_index_page(r.raise_for_status().text, cik)
    sic, src = (sic, "filing") if sic or not current_sic.isdigit() else (int(current_sic), "current")
    row = {"cik": cik, "acc": acc, "filed": f["filed"], "form": f["form"], "sic_filing": sic or "", "sic_src": src}
    if not in_group(sic):
        return row | {"in_group": 0, "eligible_text": 0}
    if primary is None:
        raise ValueError("no primary document")
    text = html_to_text(fetch(primary).raise_for_status().content.decode("utf-8", "replace"))
    return row | {"in_group": 1} | score_text(text, sic) | {"symbols": symbols(text)}


def step_score(year: int | None = None, limit: int | None = None) -> None:
    out = DATA / "scores.csv"
    done = {r["acc"] for r in read_csv(out)}
    sics = {r["cik"]: r["sic"] for r in read_csv(DATA / "companies.csv")}
    todo = sorted((c for c in read_csv(DATA / "candidates.csv")
                   if c["acc"] not in done and (year is None or c["filed"][:4] == str(year))),
                  key=lambda c: (c["filed"], c["acc"]))[:limit]
    new, t0 = not out.exists(), time.time()
    with open(out, "a", newline="") as fh, ThreadPoolExecutor(WORKERS) as ex:
        w = csv.DictWriter(fh, SCORE_COLS)
        w.writeheader() if new else None
        for i, (f, (row, err)) in enumerate(zip(todo, ex.map(lambda c: guard(score_filing, c, sics.get(c["cik"], "")), todo)), 1):
            w.writerow(row) if row else log_error("score", f["acc"], err)
            fh.flush()
            if i % 200 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"score {i}/{len(todo)} {i / el * 60:.0f}/min ETA {(len(todo) - i) * el / i / 60:.0f} min",
                      flush=True)


# ---------- step 4: summary ----------

def step_summary(year: int | None = None) -> None:
    index, cand, scores = (read_csv(DATA / n) for n in ("tenk_index.csv", "candidates.csv", "scores.csv"))
    names = {r["cik"]: r["company"] for r in index}
    names |= {r["cik"]: r["name"] for r in read_csv(DATA / "companies.csv") if r["name"]}
    years = sorted({r["filed"][:4] for r in index if year is None or r["filed"][:4] == str(year)})
    cnt = lambda rows, y, k=None: sum(r["filed"][:4] == y and (k is None or r[k] == "1") for r in rows)  # noqa: E731
    lines = ["# PIT 10-K scan summary (ADR-007)", "", "| filing year | 10-K | candidates | scored | in_group | eligible_text |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {y} | {cnt(index, y)} | {cnt(cand, y)} | {cnt(scores, y)} | {cnt(scores, y, 'in_group')} | "
              f"{cnt(scores, y, 'eligible_text')} |" for y in years]
    for y in years:
        top = sorted((r for r in scores if r["filed"][:4] == y and r["eligible_text"] == "1"),
                     key=lambda r: -float(r["per10k_total"]))[:30]
        if top:
            lines += ["", f"## {y}: top {len(top)} eligible by per10k_total", "",
                      "| cik | name | sic | per10k_total | per10k_power | acc |", "|---|---|---|---|---|---|"]
            lines += [f"| {r['cik']} | {names.get(r['cik'], '')} | {r['sic_filing']} | {r['per10k_total']} | "
                      f"{r['per10k_power']} | {r['acc']} |" for r in top]
    (HERE / "scan_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:len(years) + 4]))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m research.pit_universe.scan")
    ap.add_argument("step", choices=["index", "sic", "score", "summary"])
    for flag in ("--year", "--limit"):
        ap.add_argument(flag, type=int)
    a = ap.parse_args(argv)
    DATA.mkdir(parents=True, exist_ok=True)
    {"index": step_index, "sic": step_sic, "score": lambda: step_score(a.year, a.limit),
     "summary": lambda: step_summary(a.year)}[a.step]()


if __name__ == "__main__":
    main()
