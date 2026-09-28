"""SEC EDGAR companyfacts with strict point-in-time fact selection.

Periods are identified only by (start, end). fy/fp/frame describe the filing, never the period.
A fact is known at `asof` iff filed < asof (usable from the day after filing).
"""
import gzip
import json
import time
from datetime import date, timedelta

import httpx

from finres import config

EDGAR_DIR = config.DATA / "edgar"
HEADERS = {"User-Agent": config.SEC_UA, "Accept-Encoding": "gzip"}
MIN_INTERVAL = 1 / 8  # <= 8 requests per second
_last_call = 0.0

REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"]
COST = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"]
GROSS = ["GrossProfit"]
NET_INCOME = ["NetIncomeLoss", "ProfitLoss"]
ASSETS = ["Assets"]
SHARES = ["EntityCommonStockSharesOutstanding"]  # dei
SHARES_FALLBACK = ["CommonStockSharesOutstanding"]  # us-gaap

CLASSES = {"quarter": (80, 100), "half": (170, 200), "nine": (260, 290), "annual": (350, 380)}
MAX_AGE_DAYS = 500  # a period ending longer ago than this is stale (company stopped using the tag / stopped filing)


# ---------- network + cache ----------

def _get(url: str) -> httpx.Response:
    """GET with SEC headers and a module-level rate limit."""
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    return httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)


def _fresh(path, max_age_days: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_days * 86400


def cik_map() -> dict[str, int]:
    """Ticker -> CIK from SEC company_tickers.json (cached, refreshed after 7 days)."""
    EDGAR_DIR.mkdir(parents=True, exist_ok=True)
    path = EDGAR_DIR / "company_tickers.json"
    if not _fresh(path, 7):
        try:
            r = _get("https://www.sec.gov/files/company_tickers.json")
            r.raise_for_status()
            path.write_bytes(r.content)
        except httpx.HTTPError:
            if not path.exists():
                raise
    rows = json.loads(path.read_text()).values()
    return {row["ticker"].upper(): int(row["cik_str"]) for row in rows}


def companyfacts(ticker: str, max_age_days: float = 7) -> dict | None:
    """companyfacts JSON for a ticker (gzipped cache). None if no CIK, 404, or no us-gaap facts (foreign filers)."""
    t = ticker.upper()
    ciks = cik_map()
    cik = ciks.get(t) or ciks.get(t.replace(".", "-"))
    if cik is None:
        return None
    path = EDGAR_DIR / f"{t}.json.gz"
    if not _fresh(path, max_age_days):
        try:
            r = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            path.write_bytes(gzip.compress(r.content))
        except httpx.HTTPError:
            if not path.exists():
                raise
    data = json.loads(gzip.decompress(path.read_bytes()))
    return data if data.get("facts", {}).get("us-gaap") else None


# ---------- 8-K "hard news" (risk warning only, never a buy/sell rule) ----------

SEVERE_8K = {"1.02": "material agreement terminated", "1.03": "bankruptcy/receivership", "3.01": "delisting notice",
             "4.01": "auditor change", "4.02": "prior financials unreliable (restatement)"}


def severe_8k(sub: dict | None, asof: date, days: int = 45) -> list[dict]:
    """8-K / 8-K/A filings with a SEVERE_8K item, filed in (asof - days, asof): strictly before asof (PIT)."""
    rec = (sub or {}).get("filings", {}).get("recent", {})
    lo, out = (asof - timedelta(days=days)).isoformat(), []
    for form, d, items in zip(rec.get("form", []), rec.get("filingDate", []), rec.get("items", [])):
        sev = [i for i in (s.strip() for s in (items or "").split(",")) if i in SEVERE_8K]
        if form in ("8-K", "8-K/A") and lo < d < asof.isoformat() and sev:
            out.append({"date": d, "items": sev, "labels": [SEVERE_8K[i] for i in sev]})
    return sorted(out, key=lambda f: f["date"], reverse=True)


def recent_8k(ticker: str, asof: date, days: int = 45, max_age_days: float | None = 1) -> list[dict]:
    """Severe recent 8-Ks from the submissions JSON (gzip cache). max_age_days=None: cache only, never network."""
    t = ticker.upper()
    path = EDGAR_DIR / f"{t}.sub.json.gz"
    if max_age_days is not None and not _fresh(path, max_age_days):
        ciks = cik_map()
        cik = ciks.get(t) or ciks.get(t.replace(".", "-"))
        if cik is None:
            return []
        try:
            r = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            if r.status_code == 404:
                return []
            r.raise_for_status()
            path.write_bytes(gzip.compress(r.content))
        except httpx.HTTPError:
            if not path.exists():
                raise
    return severe_8k(json.loads(gzip.decompress(path.read_bytes())), asof, days) if path.exists() else []


# ---------- parsing ----------

def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _classify(start: date | None, end: date) -> str | None:
    """Period class by duration in days; None for instants or odd lengths."""
    if start is None:
        return None
    days = (end - start).days
    return next((k for k, (lo, hi) in CLASSES.items() if lo <= days <= hi), None)


def _items(facts: dict, concepts: list[str], unit: str, taxonomy: str = "us-gaap") -> list[dict]:
    """All items across the concept fallback list, parsed, tagged with `rank` (index in `concepts`)."""
    tax = (facts or {}).get("facts", {}).get(taxonomy, {})
    out = []
    for rank, concept in enumerate(concepts):
        for it in tax.get(concept, {}).get("units", {}).get(unit, []):
            if it.get("val") is None or not it.get("end") or not it.get("filed"):
                continue
            out.append({"start": _d(it.get("start")), "end": _d(it["end"]), "val": float(it["val"]),
                        "filed": _d(it["filed"]), "form": it.get("form"), "rank": rank})
    return out


def _known(items: list[dict], asof: date) -> dict[tuple, dict]:
    """One item per (start, end) known at asof: filed < asof; best concept rank, then latest filed."""
    best: dict[tuple, dict] = {}
    for it in items:
        if it["filed"] >= asof:
            continue
        key = (it["start"], it["end"])
        cur = best.get(key)
        if cur is None or (it["rank"], -it["filed"].toordinal()) < (cur["rank"], -cur["filed"].toordinal()):
            best[key] = it
    return best


def ttm(facts: dict, concepts: list[str], asof: date, end_on_or_before: date | None = None,
        unit: str = "USD") -> tuple[float, date] | None:
    """Trailing-twelve-month value known at asof: FY + current YTD - prior-year YTD, else latest FY."""
    known = [it for it in _known(_items(facts, concepts, unit), asof).values()
             if end_on_or_before is None or it["end"] <= end_on_or_before]
    annual = [it for it in known if _classify(it["start"], it["end"]) == "annual"]
    if not annual:
        return None
    fy = max(annual, key=lambda it: it["end"])
    lo, hi = fy["end"] - timedelta(days=5), fy["end"] + timedelta(days=5)
    ytds = [it for it in known if _classify(it["start"], it["end"]) in ("quarter", "half", "nine")
            and lo < it["start"] <= hi and it["end"] > fy["end"]]
    if ytds:
        ytd = max(ytds, key=lambda it: it["end"])
        dur = (ytd["end"] - ytd["start"]).days
        prior = [it for it in known if it["start"] is not None
                 and abs((it["end"] - it["start"]).days - dur) <= 10
                 and abs((ytd["end"] - it["end"]).days - 365) <= 10]
        if prior:
            p = min(prior, key=lambda it: abs((ytd["end"] - it["end"]).days - 365))
            return fy["val"] + ytd["val"] - p["val"], ytd["end"]
    return fy["val"], fy["end"]


def instant(facts: dict, concepts: list[str], asof: date, unit: str = "USD",
            taxonomy: str = "us-gaap") -> tuple[float, date] | None:
    """Latest point-in-time (instant) value known at asof."""
    known = [it for it in _known(_items(facts, concepts, unit, taxonomy), asof).values() if it["start"] is None]
    if not known:
        return None
    it = max(known, key=lambda it: it["end"])
    return it["val"], it["end"]


def _fresh_at(v: tuple[float, date] | None, asof: date) -> tuple[float, date] | None:
    """Drop values whose period ended more than MAX_AGE_DAYS before asof."""
    return v if v and (asof - v[1]).days <= MAX_AGE_DAYS else None


def fundamentals(facts: dict | None, asof: date) -> dict:
    """Point-in-time fundamentals as of `asof`; missing or stale pieces are None."""
    facts = facts or {}
    rev = _fresh_at(ttm(facts, REVENUE, asof), asof)
    prev = ttm(facts, REVENUE, asof, end_on_or_before=rev[1] - timedelta(days=330)) if rev else None
    gross = _fresh_at(ttm(facts, GROSS, asof), asof)
    if gross is None and rev:
        cost = ttm(facts, COST, asof)
        if cost and cost[1] == rev[1]:
            gross = (rev[0] - cost[0], rev[1])
    assets = _fresh_at(instant(facts, ASSETS, asof), asof)
    ni = _fresh_at(ttm(facts, NET_INCOME, asof), asof)
    shares = _fresh_at(instant(facts, SHARES, asof, unit="shares", taxonomy="dei")
                       or instant(facts, SHARES_FALLBACK, asof, unit="shares"), asof)
    gp, a = (gross[0] if gross else None), (assets[0] if assets else None)
    return {
        "revenue_ttm": rev[0] if rev else None,
        "revenue_ttm_prev": prev[0] if prev else None,
        "revenue_growth": rev[0] / prev[0] - 1 if rev and prev and prev[0] > 0 else None,
        "gross_profit_ttm": gp,
        "assets": a,
        "gp_assets": gp / a if gp is not None and a else None,
        "net_income_ttm": ni[0] if ni else None,
        "shares": shares[0] if shares else None,
        "fund_end": rev[1].isoformat() if rev else None,
        "pit": True,
    }
